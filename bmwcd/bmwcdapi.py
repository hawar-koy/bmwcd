#! /usr/bin/env python3
""" BMW ConnectedDrive API
Attributes:
    username (int): BMW ConnectedDrive username (email)
    password (string): BMW ConnectedDrive password
    url(string): URL you use to login to BMW ConnectedDrive, e.g. 'www.bmw-connecteddrive.nl' or 'www.bmw-connecteddrive.de'
"""

# **** bmw_connecteddrive.py ****
#
# Query vehicle data from the BMW ConnectedDrive Website, i.e. for BMW i3
# Based on the excellent work by Sergej Mueller
# https://github.com/sergejmueller/battery.ebiene.de and
# https://github.com/jupe76/bmwcdapi
#
# Permission to use, copy, modify, and distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

# ----======================================================================================================----
# This version is made by Gerard for use in Home Assistant and based on the above bmwcdapi.py script from jupe76
#
# Also inspiration came from https://github.com/frankjoke/ioBroker.bmw/blob/master/connectedDrive.js and
# https://www.symcon.de/forum/threads/36747-BMW-connected-drive-in-IPS?p=349074
# ----======================================================================================================----

import logging
import sys
import json
import time
import urllib.parse
import re
import argparse
import xml.etree.ElementTree as etree
from multiprocessing import RLock
from datetime import datetime
import requests
from requests.exceptions import HTTPError

# Print logger info when started from CLI
root = logging.getLogger()
root.setLevel(logging.INFO)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(message)s')
ch.setFormatter(formatter)
root.addHandler(ch)

##################################################################################################################################
# Enter the data below between quotes to be able to run the script from CLI
USERNAME = None         # Your BMW ConnectedDrive username
PASSWORD = None         # Your BMW ConnectedDrive password
#######################################################################################################################################
# Optional data below
URL = None              # URL without 'https://' to login to BMW ConnectedDrive, e.g. 'www.bmw-connecteddrive.nl' which is default
UPDATE_INTERVAL = 600   # The interval (sec) to check the API, don't hammer it, default is 600 sec (10 minutes), minimum is 120 seconds
#######################################################################################################################################

_LOGGER = logging.getLogger(__name__)
TIMEOUT = 10

AUTH_API = 'https://customer.bmwgroup.com/gcdm/oauth/authenticate'
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:57.0) Gecko/20100101 Firefox/57.0"

### Examples of the urls which can be used
# https://www.bmw-connecteddrive.de/api/vehicle/navigation/v1/VIN
# https://www.bmw-connecteddrive.de/api/vehicle/image/v1/VIN?startAngle=10&stepAngle=10&width=780
# https://www.bmw-connecteddrive.de/api/vehicle/dynamic/v1/VIN?offset=-60
# https://www.bmw-connecteddrive.de/api/vehicle/efficiency/v1/VIN
# https://www.bmw-connecteddrive.de/api/me/vehicles/v2 --> CARS
# https://www.bmw-connecteddrive.de/api/me/service/mapupdate/download/v1/VIN
# https://www.bmw-connecteddrive.de/api/vehicle/specs/v1/VIN
# https://www.bmw-connecteddrive.de/api/vehicle/service/v1/VIN
# https://www.bmw-connecteddrive.de/api/vehicle/servicepartner/v1/VIN
# https://www.bmw-connecteddrive.de/api/vehicle/remoteservices/chargingprofile/v1/VIN
# https://www.bmw-connecteddrive.de/api/vehicle/remoteservices/v1/VIN/history
###

class ConnectedDrive(object):
    """ BMW ConnectedDrive """
    def __init__(self, username=USERNAME, password=PASSWORD, url=URL, update_interval=UPDATE_INTERVAL):
        self._lock = RLock()
        self.printall = False
        self.bmw_username = username
        self.bmw_password = password
        if url is None:
            self.bmw_url = 'https://www.bmw-connecteddrive.nl/api/vehicle'
            self.bmw_url_me = 'https://www.bmw-connecteddrive.nl/api/me'
        else:
            if url.startswith('https://'):
                self.bmw_url = '{}/api/vehicle'.format(url)
                self.bmw_url_me = '{}/api/me'.format(url)
            else:
                self.bmw_url = 'https://{}/api/vehicle'.format(url)
                self.bmw_url_me = 'https://{}/api/me'.format(url)
        ###self.update_interval = max(update_interval, 120)    # minimum interval is 120 seconds
        self.update_interval = 120
        self.is_valid_session = False
        self.last_update_time = 0
        self.is_updated = False
        self.accesstoken = None
        self.token_expires = 0
        self.token_expires_date_time = 0
        self.utc_offset_min = 0
        self.ignore_interval = None
        self.cars = []
        self.bmw_vin = None
        self.utc_offset_min = int(round((datetime.utcnow() - datetime.now()).total_seconds()) / 60)
        _LOGGER.debug("BMW ConnectedDrive API - UTC offset: %s minutes", self.utc_offset_min)
      
        self.generate_credentials() # Get credentials
        if self.is_valid_session:   # Get data
            self.get_cars()         # Get a list with the registered cars
            self.update()           # Get the latest data for all cars in a list

    def update(self):
        """ Simple BMW ConnectedDrive API.
            Updates every x minutes as set in the update interval.
        """
        cur_time = time.time()
        with self._lock:
            if cur_time - self.last_update_time > self.update_interval:
                self.cars_data = []                                 # Make the list before loading the new data
                for car in self.cars:                               # Multiple cars can be registered for a single user
                    bmw_vin = car['vin']                            # Get the VIN
                    car_name = '{} {}'.format(car['brand'], car['modelName'])
                    car_data = self.get_car_data(bmw_vin)           # Get data for this vin
                    # Check which data is fetched, if <> 200 the error number will be returned from self.get_car_data(bmw_vin)
                    if type(car_data) is int:
                        _LOGGER.error("BMW ConnectedDrive API: data could not be fetched, error code %s", car_data)
                        return
                    _LOGGER.error("BMW ConnectedDrive API: car data %s", car_data)  ###debug
                    car_data['vin'] = bmw_vin                       # Add VIN to dict           
                    car_data['car_name'] = car_name                 # Add car name to dict
                    if 'charging_status' in car_data:
                        if car_data['remaining_fuel'] == '0':
                            type_of_car = 'electric'
                        else:
                            type_of_car = 'hybrid'
                    else:
                        type_of_car = 'fuel'
                    car_data['type_of_car'] = type_of_car           # Add car type to dict
                    _LOGGER.info("%s: type of car: %s", car_data['car_name'], type_of_car)
                    self.cars_data.append(car_data)                 # Make a list for every car
                    _LOGGER.info("BMW ConnectedDrive API: data collected from %s", car_name)
                _LOGGER.error("BMW ConnectedDrive API: data for all cars  %s", self.cars_data)
                self.last_update_time = time.time()
                self.is_updated = True
                
                # Print some data when started from CLI
                for car in self.cars_data:
                    print('--------------START CAR DATA--------------')
                    for k, v in sorted(car.items()):
                        print("{}: {}".format(k, v))
                    print('--------------END CAR DATA--------------')

                return self.cars_data
            else:
                _LOGGER.error("BMW ConnectedDrive API: no data collected from car as interval time has not yet passed.")
                self.is_updated = False
                return

    def token_valid(self):
        """Check if token is still valid, if not make new token."""
        cur_time = time.time()
        if int(cur_time) >= int(self.token_expires):
            self.generate_credentials()
            _LOGGER.debug("BMW ConnectedDrive API: new credentials obtained (token expires at: %s)",
                         self.token_expires_date_time)
        else:
            _LOGGER.debug("BMW ConnectedDrive API: current credentials still valid (token expires at: %s)",
                         self.token_expires_date_time)

    def generate_credentials(self):
        """If previous token has expired, create a new one."""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-agent": USER_AGENT
        }
        values = {
            'username' : self.bmw_username,
            'password' : self.bmw_password,
            'client_id' : 'dbf0a542-ebd1-4ff0-a9a7-55172fbfce35',
            'redirect_uri' : 'https://www.bmw-connecteddrive.com/app/default/static/external-dispatch.html',
            'response_type' : 'token',
            'scope' : 'authenticate_user fupo',
            'state' : 'eyJtYXJrZXQiOiJkZSIsImxhbmd1YWdlIjoiZGUiLCJkZXN0aW5hdGlvbiI6ImxhbmRpbmdQYWdlIn0',
            'locale' : 'DE-de'
        }

        data = urllib.parse.urlencode(values)
        credentials_response = requests.post(AUTH_API, data=data, headers=headers, allow_redirects=False)
        # credentials_response.statuscode will be 302
        _LOGGER.debug("BMW ConnectedDrive API: credentials response code: %s",
                      credentials_response.status_code)
        
        # https://www.bmw-connecteddrive.com/app/default/static/external-dispatch.html?error=access_denied
        my_payload = credentials_response.headers['Location']
        if 'error=access_denied' in my_payload:
            self.is_valid_session = False
        else:
            result_m = re.match(".*access_token=([\w\d]+).*token_type=(\w+).*expires_in=(\d+).*", my_payload)
            
            token_type = result_m.group(2)
            _LOGGER.debug("BMW ConnectedDrive API: token type: %s", token_type)
            self.accesstoken = result_m.group(1)
            self.token_expires = int(time.time()) + int(result_m.group(3))
            self.token_expires_date_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.token_expires))
            self.is_valid_session = True
        return

    def request_car_data(self, data_type, sub_data_type=None, vin=None):
        """Get data from BMW Connected Drive."""
        headers = {
            'Content-Type': 'application/json',
            'User-agent': USER_AGENT,
            'Authorization' : 'Bearer ' + self.accesstoken
        }

        self.token_valid()  # Check if current token is still valid
        if vin is not None:
            self.bmw_vin = vin

        if data_type == 'dynamic':
            url = '{}/{}/v1/{}?offset={}'.format(self.bmw_url, data_type, self.bmw_vin, str(self.utc_offset_min))
        elif data_type == 'get_cars':
            url = '{}/vehicles/v2'.format(self.bmw_url_me) # https://www.bmw-connecteddrive.nl/api/me/vehicles/v2
        else:
            url = '{}/{}/v1/{}'.format(self.bmw_url, data_type, self.bmw_vin)
        
        data_response = requests.get(url,
                                     headers=headers,
                                     allow_redirects=True) ###Timeout
        
        if data_response.status_code == 200:
            _LOGGER.info("BMW ConnectedDrive API: connect to URL %s", url)
            if data_type == 'dynamic' or data_type == 'servicepartner':
                return data_response.json()[sub_data_type]
            else:
                return data_response.json()
        else:
            _LOGGER.error("BMW ConnectedDrive API: error code %s while getting data", data_response.status_code) ### Status melding nog toevoegen
            
        return data_response.status_code    ### was return False
    
    def get_cars(self):
        """Get car data from BMW Connected Drive."""  
        self.cars = self.request_car_data('get_cars')
        ###AANPASSEN NAAR NETTE MELDING IN LOG, INCL REFERENTIE NAAR API
        for car in self.cars:
            _LOGGER.info("BMW ConnectedDrive API - Car: %s %s, Vin: %s", car['brand'], car['modelName'], car['vin'])

        if self.printall:
            _LOGGER.info('--------------START CARS--------------')
            # Contains a list with a dict in it
            for car in self.cars:
                car_number = 1
                _LOGGER.info("Car %s of %s", car_number, str(len(self.cars)))
                for key in sorted(car):
                    _LOGGER.info("%s: %s", key, car[key])
                car_number += 1
            _LOGGER.info('--------------END CARS--------------')

        return self.cars

    def get_car_data(self, vin):
        """Get car data from BMW Connected Drive.""" 
        return self.request_car_data('dynamic', 'attributesMap', vin)

    def get_car_location(self, vin):
        """Get car location from BMW Connected Drive."""
        return self.request_car_data('dynamic', 'attributesMap', vin)

    def get_car_data_service(self, vin):
        """Get car data from BMW Connected Drive."""
        map_car_data_service = self.request_car_data('dynamic', 'vehicleMessages', vin)

        if self.printall:
            _LOGGER.info('--------------START CAR DATA SERVICE--------------')
            for key in sorted(map_car_data_service):
                _LOGGER.info("%s: %s", key, map_car_data_service[key])
            _LOGGER.info('--------------END CAR DATA SERVICE--------------')

        return map_car_data_service

    def get_car_navigation(self, vin):
        """Get navigation data from BMW Connected Drive."""
        map_car_navigation = self.request_car_data('navigation', vin)

        if self.printall:
            _LOGGER.info('--------------START CAR NAV--------------')
            for key in sorted(map_car_navigation):
                _LOGGER.info("%s: %s" % (key, map_car_navigation[key]))
            _LOGGER.info('--------------END CAR NAV--------------')

        return map_car_navigation

    def get_car_efficiency(self, vin):
        """Get efficiency data from BMW Connected Drive."""
        map_car_efficiency = self.request_car_data('efficiency', vin)

        if self.printall:
            _LOGGER.info('--------------START CAR EFFICIENCY--------------')
            for key in sorted(map_car_efficiency):
                _LOGGER.info("%s: %s" % (key, map_car_efficiency[key]))
            _LOGGER.info('--------------END CAR EFFICIENCY--------------')

        return map_car_efficiency

    def get_car_service_partner(self, vin):
        """Get servicepartner data from BMW Connected Drive."""       
        map_car_service_partner = self.request_car_data('servicepartner', 'dealer', vin)

        if self.printall:
            _LOGGER.info('--------------START CAR SERVICEPARTNER--------------')
            for key in sorted(map_car_service_partner):
                _LOGGER.info("%s: %s" % (key, map_car_service_partner[key]))
            _LOGGER.info('--------------END CAR SERVICEPARTNER--------------')

        return map_car_service_partner

    def execute_service(self, service, vin):
        """Get servicepartner data from BMW Connected Drive."""
        self.token_valid()  # Check if current token is still valid

        max_retries = 9
        interval = 10 #secs

        service_codes = {
            'climate': 'RCN',
            'lock': 'RDL',
            'unlock': 'RDU',
            'light': 'RLF',
            'horn': 'RHB'
        }

        headers = {
            "Content-Type": "application/json",
            "User-agent": USER_AGENT,
            "Authorization" : "Bearer "+ self.accesstoken
        }

        _LOGGER.info("BMW ConnectedDrive API - executing service %s", service)
        command = service_codes[service]
        remote_service_status = None
        url = '{}/remoteservices/v1/{}/{}'.format(self.bmw_url, vin, command)
        url_check = '{}/remoteservices/v1/{}/state/execution'.format(self.bmw_url, vin)

        execute_response = requests.post(url,
                                         headers=headers,
                                         allow_redirects=True)

        if execute_response.status_code != 200:
            _LOGGER.error("BMW ConnectedDrive API - error during executing service %s", service)
            return False

        for i in range(max_retries):
            time.sleep(interval)
            remoteservices_response = requests.get(url_check,
                                                   headers=headers,
                                                   allow_redirects=True)
            _LOGGER.debug("BMW ConnectedDrive API - status execstate %s %s", str(remoteservices_response.status_code), remoteservices_response.text)
            root_data = etree.fromstring(remoteservices_response.text)
            remote_service_status = root_data.find('remoteServiceStatus').text
            #print(remoteServiceStatus)
            if remote_service_status == 'EXECUTED':
                _LOGGER.info("BMW ConnectedDrive API - executing service %s succeeded", service)
                break

        if remote_service_status != 'EXECUTED':
            _LOGGER.error("BMW ConnectedDrive API - error during executing service %s, timer expired", service)
            return False

        return True

def main():
    """Show information when this script is started from CLI."""
    _LOGGER.info("Running script to get data from BMW ConnectedDrive")
    c = ConnectedDrive()

    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--printall', action='store_true',
                        help='print all values that were received')
    parser.add_argument('-e', '--execservice', dest='service',
                        choices=['climate', 'lock', 'unlock', 'light', 'horn'],
                        action='store', help='execute service like instant climate control')
    args = vars(parser.parse_args())

    if args["printall"]:
        c.printall = True

    ### UPDATE BELOW FOR VIN IN EXECUTE FUNCTION
    # dont query data and execute the service at the same time, takes too long
    #if args["service"]:
        # execute service
    #    c.execute_service(args["service"])
    #else:
        #c.update()
        #c.get_car_data()
        #c.get_cars()
        #c.get_car_navigation()
        #c.get_car_efficiency()
        #c.get_car_service_partner()

    return

if __name__ == '__main__':
    main()
