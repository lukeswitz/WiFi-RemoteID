#!/usr/bin/env python3
# MIT License
# Copyright (c) 2025 Luke Switzer
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights 
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell 
# copies of the Software, and to permit persons to whom the Software is 
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in 
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR 
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, 
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE 
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER 
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, 
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN 
# THE SOFTWARE.

import os
import time
import json
import csv
import logging
import threading
import argparse
import subprocess
import socket
import requests
import urllib3
import serial
import serial.tools.list_ports
import zmq
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import Optional, List, Dict, Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("mesh_mapper_headless.log")
    ]
)
logger = logging.getLogger("mesh-mapper-headless")

# Initialize global variables
tracked_pairs = {}
detection_history = []

# Serial connection tracking
zmq_contexts = {}
zmq_sockets = {}
zmq_threads = {}
SELECTED_PORTS = {}
BAUD_RATE = 115200
staleThreshold = 60  # Default stale threshold in seconds
serial_connected_status = {}
last_mac_by_port = {}
serial_objs = {}
serial_objs_lock = threading.Lock()

# Webhook URL
WEBHOOK_URL = None

class MeshMapper:
    def __init__(self, args):
        self.args = args
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Apply command-line arguments
        self.webhook_url = args.webhook_url
        global staleThreshold, WEBHOOK_URL
        if args.stale_threshold:
            staleThreshold = args.stale_threshold * 60  # Convert minutes to seconds
            
        if self.webhook_url:
            WEBHOOK_URL = self.webhook_url
            logger.info(f"Setting webhook URL: {WEBHOOK_URL}")
            
            # Setup file paths
        self.startup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.setup_file_paths()
        
        # Load aliases and FAA cache
        self.aliases = self.load_aliases()
        self.faa_cache = self.load_faa_cache()
        
        # Initialize detection tracking
        self.write_csv_headers()
        self.initialize_cumulative_kml()
        
    def setup_file_paths(self):
        """Setup file paths for log and data files"""
        # Use provided output directory or default to current directory
        output_dir = Path(self.args.output_dir or self.base_dir)
        output_dir.mkdir(exist_ok=True)
        
        logger.info(f"Using output directory: {output_dir}")
        
        # Setup file paths
        self.csv_filename = output_dir / f"detections_{self.startup_timestamp}.csv"
        self.kml_filename = output_dir / f"detections_{self.startup_timestamp}.kml"
        self.faa_log_filename = output_dir / "faa_log.csv"
        self.cumulative_kml_filename = output_dir / "cumulative.kml"
        self.cumulative_csv_filename = output_dir / "cumulative_detections.csv"
        self.aliases_file = output_dir / "aliases.json"
        self.faa_cache_file = output_dir / "faa_cache.csv"
        
        logger.info(f"Using CSV file: {self.csv_filename}")
        logger.info(f"Using KML file: {self.kml_filename}")
        
    def load_aliases(self):
        """Load aliases from file"""
        aliases = {}
        if os.path.exists(self.aliases_file):
            try:
                with open(self.aliases_file, "r") as f:
                    aliases = json.load(f)
                logger.info(f"Loaded {len(aliases)} aliases from {self.aliases_file}")
            except Exception as e:
                logger.error(f"Error loading aliases: {e}")
        return aliases
    
    def save_aliases(self):
        """Save aliases to file"""
        try:
            with open(self.aliases_file, "w") as f:
                json.dump(self.aliases, f)
            logger.info(f"Saved {len(self.aliases)} aliases to {self.aliases_file}")
        except Exception as e:
            logger.error(f"Error saving aliases: {e}")
            
    def load_faa_cache(self):
        """Load FAA cache from file"""
        faa_cache = {}
        if os.path.exists(self.faa_cache_file):
            try:
                with open(self.faa_cache_file, newline='') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        key = (row['mac'], row['remote_id'])
                        faa_cache[key] = json.loads(row['faa_response'])
                logger.info(f"Loaded {len(faa_cache)} FAA cache entries from {self.faa_cache_file}")
            except Exception as e:
                logger.error(f"Error loading FAA cache: {e}")
        return faa_cache
    
    def write_to_faa_cache(self, mac, remote_id, faa_data):
        """Write to FAA cache file"""
        key = (mac, remote_id)
        self.faa_cache[key] = faa_data
        try:
            file_exists = os.path.isfile(self.faa_cache_file)
            with open(self.faa_cache_file, "a", newline='') as csvfile:
                fieldnames = ["mac", "remote_id", "faa_response"]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerow({
                    "mac": mac,
                    "remote_id": remote_id,
                    "faa_response": json.dumps(faa_data)
                })
            logger.debug(f"Added FAA cache entry for {mac}/{remote_id}")
        except Exception as e:
            logger.error(f"Error writing to FAA cache: {e}")
            
    def write_csv_headers(self):
        """Initialize CSV files with headers"""
        # Detection CSV header
        with open(self.csv_filename, mode='w', newline='') as csvfile:
            fieldnames = [
                'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
                'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            # Cumulative CSV header - if it doesn't exist
        if not os.path.exists(self.cumulative_csv_filename):
            with open(self.cumulative_csv_filename, mode='w', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=[
                    'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
                    'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
                ])
                writer.writeheader()
                
                # FAA log CSV header - if it doesn't exist
        if not os.path.exists(self.faa_log_filename):
            with open(self.faa_log_filename, mode='w', newline='') as csvfile:
                fieldnames = ['timestamp', 'mac', 'remote_id', 'faa_response']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
    def initialize_cumulative_kml(self):
        """Initialize cumulative KML file if it doesn't exist"""
        if not os.path.exists(self.cumulative_kml_filename):
            with open(self.cumulative_kml_filename, "w") as f:
                f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
                f.write('<kml xmlns="http://www.opengis.net/kml/2.2">\n')
                f.write('<Document>\n')
                f.write(f'<name>Cumulative Detections</name>\n')
                f.write('</Document>\n</kml>')
                
    def generate_kml(self):
        """Generate KML file from tracked pairs"""
        kml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<kml xmlns="http://www.opengis.net/kml/2.2">',
            '<Document>',
            f'<name>Detections {self.startup_timestamp}</name>'
        ]
        for mac, det in tracked_pairs.items():
            alias = self.aliases.get(mac, '')
            aliasStr = f"{alias} " if alias else ""
            remoteIdStr = ""
            if det.get("basic_id"):
                remoteIdStr = " (RemoteID: " + det.get("basic_id") + ")"
            if det.get("faa_data"):
                remoteIdStr += " FAA: " + json.dumps(det.get("faa_data"))
                
                # Drone placemark
            if det.get("drone_lat", 0) != 0 and det.get("drone_long", 0) != 0:
                kml_lines.append(f'<Placemark><name>Drone {aliasStr}{mac}{remoteIdStr}</name>')
                kml_lines.append('<Style><IconStyle><scale>1.2</scale>'
                    '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/heliport.png</href></Icon>'
                    '</IconStyle></Style>')
                kml_lines.append(f'<Point><coordinates>{det.get("drone_long",0)},{det.get("drone_lat",0)},0</coordinates></Point>')
                kml_lines.append('</Placemark>')
                
                # Pilot placemark
            if det.get("pilot_lat", 0) != 0 and det.get("pilot_long", 0) != 0:
                kml_lines.append(f'<Placemark><name>Pilot {aliasStr}{mac}{remoteIdStr}</name>')
                kml_lines.append('<Style><IconStyle><scale>1.2</scale>'
                    '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/man.png</href></Icon>'
                    '</IconStyle></Style>')
                kml_lines.append(f'<Point><coordinates>{det.get("pilot_long",0)},{det.get("pilot_lat",0)},0</coordinates></Point>')
                kml_lines.append('</Placemark>')
                
        kml_lines.append('</Document></kml>')
        with open(self.kml_filename, "w") as f:
            f.write("\n".join(kml_lines))
        logger.info(f"Updated KML file: {self.kml_filename}")
        
    def append_to_cumulative_kml(self, mac, detection):
        """Append detection to cumulative KML file"""
        alias = self.aliases.get(mac, '')
        aliasStr = f"{alias} " if alias else ""
        
        # Build placemark for drone position
        if detection.get("drone_lat", 0) != 0 and detection.get("drone_long", 0) != 0:
            placemark = [
                f"<Placemark><name>Drone {aliasStr}{mac} {datetime.now().isoformat()}</name>",
                f"<Point><coordinates>{detection['drone_long']},{detection['drone_lat']},0</coordinates></Point>",
                "</Placemark>"
            ]
            # Insert before closing tags
            with open(self.cumulative_kml_filename, "r+") as f:
                content = f.read()
                # Strip closing tags
                content = content.replace("</Document>\n</kml>", "")
                f.seek(0)
                f.write(content)
                f.write("\n" + "\n".join(placemark) + "\n</Document>\n</kml>")
                
                # Also add pilot position
        if detection.get("pilot_lat", 0) != 0 and detection.get("pilot_long", 0) != 0:
            placemark = [
                f"<Placemark><name>Pilot {aliasStr}{mac} {datetime.now().isoformat()}</name>",
                f"<Point><coordinates>{detection['pilot_long']},{detection['pilot_lat']},0</coordinates></Point>",
                "</Placemark>"
            ]
            with open(self.cumulative_kml_filename, "r+") as f:
                content = f.read()
                content = content.replace("</Document>\n</kml>", "")
                f.seek(0)
                f.write(content)
                f.write("\n" + "\n".join(placemark) + "\n</Document>\n</kml>")
                
    def update_detection(self, detection):
        """Update detection and track it"""
        mac = detection.get("mac")
        if not mac:
            return
        
        # Retrieve new drone coordinates from the detection
        new_drone_lat = detection.get("drone_lat", 0)
        new_drone_long = detection.get("drone_long", 0)
        valid_drone = (new_drone_lat != 0 and new_drone_long != 0)
        
        if not valid_drone:
            logger.info(f"No-GPS detection for {mac}; forwarding for webhook.")
            # Forward this no-GPS detection to the webhook
            tracked_pairs[mac] = detection
            detection_history.append(detection.copy())
            
            # Server-side webhook firing for no-GPS detection
            if self.webhook_url:
                try:
                    requests.post(self.webhook_url, json=detection, timeout=5)
                    logger.debug(f"Sent webhook for no-GPS detection: {mac}")
                except Exception as e:
                    logger.error(f"Server webhook error: {e}")
            return
        
        # Otherwise, use the provided non-zero coordinates.
        detection["drone_lat"] = new_drone_lat
        detection["drone_long"] = new_drone_long
        detection["drone_altitude"] = detection.get("drone_altitude", 0)
        detection["pilot_lat"] = detection.get("pilot_lat", 0)
        detection["pilot_long"] = detection.get("pilot_long", 0)
        detection["last_update"] = time.time()
        
        # Preserve previous basic_id if new detection lacks one
        if not detection.get("basic_id") and mac in tracked_pairs and tracked_pairs[mac].get("basic_id"):
            detection["basic_id"] = tracked_pairs[mac]["basic_id"]
            
        remote_id = detection.get("basic_id")
        
        # Try exact cache lookup by (mac, remote_id), then fallback to any cached data for this mac
        if mac:
            # Exact match if basic_id provided
            if remote_id:
                key = (mac, remote_id)
                if key in self.faa_cache:
                    detection["faa_data"] = self.faa_cache[key]
                    
                    # Fallback: any cached FAA data for this mac
            if "faa_data" not in detection:
                for (c_mac, _), faa_data in self.faa_cache.items():
                    if c_mac == mac:
                        detection["faa_data"] = faa_data
                        break
                    
                    # Fallback: last known FAA data in tracked_pairs
            if "faa_data" not in detection and mac in tracked_pairs and "faa_data" in tracked_pairs[mac]:
                detection["faa_data"] = tracked_pairs[mac]["faa_data"]
                
                # Always cache FAA data by MAC and current basic_id for fallback
            if "faa_data" in detection:
                self.write_to_faa_cache(mac, detection.get("basic_id", ""), detection["faa_data"])
                
        tracked_pairs[mac] = detection
        detection_history.append(detection.copy())
        
        # Send notification about new detection if configured
        if self.args.notifications:
            self.notify_detection(detection)
            
            # Webhook notification if configured 
        if self.webhook_url:
            try:
                requests.post(self.webhook_url, json=detection, timeout=5)
                logger.debug(f"Sent webhook for detection: {mac}")
            except Exception as e:
                logger.error(f"Server webhook error: {e}")
                
        logger.info(f"Updated detection: MAC={mac}, drone_lat={new_drone_lat}, drone_long={new_drone_long}")
        
        # Write to session CSV
        with open(self.csv_filename, mode='a', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=[
                'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
                'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
            ])
            writer.writerow({
                'timestamp': datetime.now().isoformat(),
                'alias': self.aliases.get(mac, ''),
                'mac': mac,
                'rssi': detection.get('rssi', ''),
                'drone_lat': detection.get('drone_lat', ''),
                'drone_long': detection.get('drone_long', ''),
                'drone_altitude': detection.get('drone_altitude', ''),
                'pilot_lat': detection.get('pilot_lat', ''),
                'pilot_long': detection.get('pilot_long', ''),
                'basic_id': detection.get('basic_id', ''),
                'faa_data': json.dumps(detection.get('faa_data', {}))
            })
            
            # Append to cumulative CSV
        with open(self.cumulative_csv_filename, mode='a', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=[
                'timestamp', 'alias', 'mac', 'rssi', 'drone_lat', 'drone_long',
                'drone_altitude', 'pilot_lat', 'pilot_long', 'basic_id', 'faa_data'
            ])
            writer.writerow({
                'timestamp': datetime.now().isoformat(),
                'alias': self.aliases.get(mac, ''),
                'mac': mac,
                'rssi': detection.get('rssi', ''),
                'drone_lat': detection.get('drone_lat', ''),
                'drone_long': detection.get('drone_long', ''),
                'drone_altitude': detection.get('drone_altitude', ''),
                'pilot_lat': detection.get('pilot_lat', ''),
                'pilot_long': detection.get('pilot_long', ''),
                'basic_id': detection.get('basic_id', ''),
                'faa_data': json.dumps(detection.get('faa_data', {}))
            })
            
            # Update KMLs
        self.generate_kml()
        self.append_to_cumulative_kml(mac, detection)
        
    def notify_detection(self, detection):
        """Send desktop notification for a new detection"""
        mac = detection.get("mac", "Unknown")
        alias = self.aliases.get(mac, mac)
        
        if os.name == 'posix':  # Linux/Mac
            try:
                title = f"Drone Detected: {alias}"
                body = f"MAC: {mac}\nPosition: {detection.get('drone_lat', 0)}, {detection.get('drone_long', 0)}"
                subprocess.run(['notify-send', title, body])
                logger.debug(f"Sent desktop notification for {mac}")
            except Exception as e:
                logger.error(f"Error sending notification: {e}")
        elif os.name == 'nt':  # Windows
            try:
                from win10toast import ToastNotifier
                toaster = ToastNotifier()
                title = f"Drone Detected: {alias}"
                msg = f"MAC: {mac}\nPosition: {detection.get('drone_lat', 0)}, {detection.get('drone_long', 0)}"
                toaster.show_toast(title, msg, duration=5)
                logger.debug(f"Sent desktop notification for {mac}")
            except Exception as e:
                logger.error(f"Error sending Windows notification: {e}")
            
    def query_faa_api(self, mac, remote_id):
        """Query the FAA API for a remote ID"""
        session = self.create_retry_session()
        self.refresh_cookie(session)
        faa_result = self.query_remote_id(session, remote_id)
        
        # Fallback: if FAA API query failed or returned no records, try cached FAA data by MAC
        if not faa_result or not faa_result.get("data", {}).get("items"):
            for (c_mac, _), cached_data in self.faa_cache.items():
                if c_mac == mac:
                    faa_result = cached_data
                    break
                
        if faa_result is None:
            logger.error(f"FAA query failed for {mac}/{remote_id}")
            return None
        
        # Update tracked_pairs with the new FAA data
        if mac in tracked_pairs:
            tracked_pairs[mac]["faa_data"] = faa_result
        else:
            tracked_pairs[mac] = {"basic_id": remote_id, "faa_data": faa_result}
            
        self.write_to_faa_cache(mac, remote_id, faa_result)
        
        # Log the FAA query
        timestamp = datetime.now().isoformat()
        try:
            with open(self.faa_log_filename, "a", newline='') as csvfile:
                fieldnames = ["timestamp", "mac", "remote_id", "faa_response"]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writerow({
                    "timestamp": timestamp,
                    "mac": mac,
                    "remote_id": remote_id,
                    "faa_response": json.dumps(faa_result)
                })
        except Exception as e:
            logger.error(f"Error writing to FAA log CSV: {e}")
            
            # Update KML
        self.generate_kml()
        return faa_result
    
    def create_retry_session(self, retries=3, backoff_factor=2, status_forcelist=(502, 503, 504)):
        """Create a retry-enabled session with custom headers for FAA query"""
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:137.0) Gecko/20100101 Firefox/137.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://uasdoc.faa.gov/listdocs",
            "client": "external"
        })
        retry = Retry(
            total=retries,
            read=retries,
            connect=retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        return session
    
    def refresh_cookie(self, session):
        """Refresh FAA cookie by requesting homepage"""
        homepage_url = "https://uasdoc.faa.gov/listdocs"
        logger.debug(f"Refreshing FAA cookie by requesting homepage: {homepage_url}")
        try:
            response = session.get(homepage_url, timeout=30)
            logger.debug(f"FAA homepage response code: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error refreshing FAA cookie: {e}")
            
    def query_remote_id(self, session, remote_id):
        """Query the FAA API for a remote ID"""
        endpoint = "https://uasdoc.faa.gov/api/v1/serialNumbers"
        params = {
            "itemsPerPage": 8,
            "pageIndex": 0,
            "orderBy[0]": "updatedAt",
            "orderBy[1]": "DESC",
            "findBy": "serialNumber",
            "serialNumber": remote_id
        }
        logger.debug(f"Querying FAA API endpoint: {endpoint} with params: {params}")
        try:
            response = session.get(endpoint, params=params, timeout=30)
            logger.debug(f"FAA Request URL: {response.url}")
            if response.status_code != 200:
                logger.error(f"FAA HTTP error: {response.status_code} - {response.reason}")
                return None
            return response.json()
        except Exception as e:
            logger.error(f"Error querying FAA API: {e}")
            return None
        
    def start_serial_thread(self, port):
        """Start a serial reader thread for a port"""
        thread = threading.Thread(target=self.serial_reader, args=(port,), daemon=True)
        thread.start()
        logger.info(f"Started serial reader thread for port {port}")
        return thread
    
    def serial_reader(self, port):
        """Serial reader function for a thread"""
        ser = None
        while True:
            # Try to open or re-open the serial port
            if ser is None or not getattr(ser, 'is_open', False):
                try:
                    ser = serial.Serial(port, BAUD_RATE, timeout=1)
                    serial_connected_status[port] = True
                    logger.info(f"Opened serial port {port} at {BAUD_RATE} baud.")
                    with serial_objs_lock:
                        serial_objs[port] = ser
                except Exception as e:
                    serial_connected_status[port] = False
                    logger.error(f"Error opening serial port {port}: {e}")
                    time.sleep(1)
                    continue
                
            try:
                # Read incoming data
                if ser.in_waiting:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                    
                    # Extract JSON
                    if '{' in line:
                        json_str = line[line.find('{'):]
                    else:
                        json_str = line
                        
                        # Parse JSON
                    try:
                        detection = json.loads(json_str)
                        # Track MAC address
                        if 'mac' in detection:
                            last_mac_by_port[port] = detection['mac']
                        elif port in last_mac_by_port:
                            detection['mac'] = last_mac_by_port[port]
                    except json.JSONDecodeError:
                        continue
                    
                    # Handle remote_id field
                    if 'remote_id' in detection and 'basic_id' not in detection:
                        detection['basic_id'] = detection['remote_id']
                        
                        # Skip heartbeat messages
                    if 'heartbeat' in detection:
                        continue
                    
                    # Process detection
                    self.update_detection(detection)
                else:
                    time.sleep(0.1)
                    
            except (serial.SerialException, OSError) as e:
                serial_connected_status[port] = False
                logger.error(f"SerialException/OSError on {port}: {e}")
                try:
                    if ser and ser.is_open:
                        ser.close()
                except Exception:
                    pass
                ser = None
                with serial_objs_lock:
                    serial_objs.pop(port, None)
                time.sleep(1)
                
            except Exception as e:
                serial_connected_status[port] = False
                logger.error(f"Unexpected error on {port}: {e}")
                try:
                    if ser and ser.is_open:
                        ser.close()
                except Exception:
                    pass
                ser = None
                with serial_objs_lock:
                    serial_objs.pop(port, None)
                time.sleep(1)
                
    def start_zmq_client(self, endpoint):
        """Start a ZMQ client for a specific endpoint"""
        global zmq_contexts, zmq_sockets, zmq_threads
        
        # Clean up any existing connection for this endpoint
        if endpoint in zmq_sockets:
            self.stop_zmq_client(endpoint)
            
        try:
            context = zmq.Context()
            socket = context.socket(zmq.SUB)
            socket.setsockopt_string(zmq.SUBSCRIBE, "")
            socket.connect(endpoint)
            logger.info(f"Connected to ZMQ endpoint: {endpoint}")
            
            zmq_contexts[endpoint] = context
            zmq_sockets[endpoint] = socket
            
            thread = threading.Thread(target=self.zmq_message_handler, args=(endpoint,), daemon=True)
            thread.start()
            zmq_threads[endpoint] = thread
            
            return True
        except Exception as e:
            logger.error(f"Failed to start ZMQ client for {endpoint}: {e}")
            if endpoint in zmq_sockets and zmq_sockets[endpoint]:
                zmq_sockets[endpoint].close()
                del zmq_sockets[endpoint]
            if endpoint in zmq_contexts and zmq_contexts[endpoint]:
                zmq_contexts[endpoint].term()
                del zmq_contexts[endpoint]
            return False
        
    def zmq_message_handler(self, endpoint):
        """Handle ZMQ messages from a specific endpoint"""
        socket = zmq_sockets.get(endpoint)
        
        while True:
            try:
                if socket is None:
                    time.sleep(1)
                    continue
                
                message = socket.recv_string(flags=zmq.NOBLOCK)
                try:
                    detection = json.loads(message)
                    # Process the ZMQ message as a detection
                    self.update_detection(detection)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from ZMQ endpoint {endpoint}: {message[:100]}")
            except zmq.Again:
                # No message available, continue
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"ZMQ error from endpoint {endpoint}: {e}")
                time.sleep(1)
                
    def stop_zmq_client(self, endpoint):
        """Stop a ZMQ client for a specific endpoint"""
        global zmq_contexts, zmq_sockets, zmq_threads
        
        if endpoint in zmq_sockets and zmq_sockets[endpoint]:
            try:
                zmq_sockets[endpoint].close()
            except:
                pass
            del zmq_sockets[endpoint]
            
        if endpoint in zmq_contexts and zmq_contexts[endpoint]:
            try:
                zmq_contexts[endpoint].term()
            except:
                pass
            del zmq_contexts[endpoint]
            
        if endpoint in zmq_threads and zmq_threads[endpoint] and zmq_threads[endpoint].is_alive():
            zmq_threads[endpoint].join(timeout=2)
            if zmq_threads[endpoint].is_alive():
                logger.warning(f"ZMQ thread for {endpoint} did not exit cleanly")
            del zmq_threads[endpoint]
            
    def stop_all_zmq_clients(self):
        """Stop all ZMQ clients"""
        global zmq_sockets
        
        endpoints = list(zmq_sockets.keys())
        for endpoint in endpoints:
            self.stop_zmq_client(endpoint)
            
    def run(self):
        """Run the main application loop"""
        logger.info("Starting Mesh-Mapper Headless...")
        
        try:
            # Initialize serial ports
            if self.args.serial_ports:
                for i, port in enumerate(self.args.serial_ports, 1):
                    port_key = f'port{i}'
                    SELECTED_PORTS[port_key] = port
                    serial_connected_status[port] = False
                    self.start_serial_thread(port)
                    logger.info(f"Initialized serial port {port}")
                    
            # Initialize ZMQ clients
            if self.args.zmq_endpoints:
                for endpoint in self.args.zmq_endpoints:
                    self.start_zmq_client(endpoint)
                    logger.info(f"Initialized ZMQ endpoint {endpoint}")
                    
            # Initial KML generation
            self.generate_kml()
            
            # Status update interval
            status_interval = self.args.status_interval
            last_status_time = time.time()
            
            # Main loop
            try:
                while True:
                    # Print periodic status updates
                    current_time = time.time()
                    if current_time - last_status_time >= status_interval:
                        self.print_status()
                        last_status_time = current_time
                        
                    # Clean up stale detections
                    global tracked_pairs
                    # Make a copy of the keys to avoid modification during iteration
                    mac_addresses = list(tracked_pairs.keys())
                    for mac in mac_addresses:
                        det = tracked_pairs[mac]
                        if current_time - det.get("last_update", 0) > staleThreshold:
                            logger.info(f"Removing stale detection: {mac}")
                            del tracked_pairs[mac]
                            
                    # Sleep to prevent CPU hogging
                    time.sleep(0.5)
                    
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
                self.cleanup()
                
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
            self.cleanup()
            
        logger.info("Mesh-Mapper Headless shutdown complete")
        
    def print_status(self):
        """Print status information to the console"""
        # Count active detections
        current_time = time.time()
        active_count = 0
        for mac, det in tracked_pairs.items():
            if current_time - det.get("last_update", 0) <= staleThreshold:
                active_count += 1
                
        # Serial port status
        serial_status = []
        for port, status in serial_connected_status.items():
            serial_status.append(f"{port}: {'Connected' if status else 'Disconnected'}")
            
        # ZMQ status
        zmq_status = [f"{endpoint}" for endpoint in zmq_sockets.keys()]
        
        logger.info("=== Mesh-Mapper Status ===")
        logger.info(f"Active detections: {active_count}")
        logger.info(f"Total historical detections: {len(detection_history)}")
        logger.info(f"Serial ports: {', '.join(serial_status) or 'None'}")
        logger.info(f"ZMQ connections: {', '.join(zmq_status) or 'None'}")
        logger.info(f"Stale threshold: {staleThreshold}s")
        logger.info(f"Output directory: {self.args.output_dir}")
        logger.info("========================")
        
        """Clean up resources before exit"""
        logger.info("Cleaning up resources...")
        
        # Close serial ports
        with serial_objs_lock:
            for port, ser in list(serial_objs.items()):
                try:
                    if ser and ser.is_open:
                        logger.info(f"Closing serial port {port}")
                        ser.close()
                except Exception as e:
                    logger.error(f"Error closing serial port {port}: {e}")
                    
        # Stop ZMQ clients
        self.stop_all_zmq_clients()
        
        # Final KML generation
        self.generate_kml()
        
        # Save aliases
        self.save_aliases()
        
        logger.info("Cleanup complete")
        
        
def main():
    """Parse command line arguments and run the application"""
    parser = argparse.ArgumentParser(description='Mesh-Mapper Headless - Drone detection and mapping tool')
    
    # Serial port options
    parser.add_argument('--serial-ports', nargs='+', help='Serial ports to use (up to 3)')
    parser.add_argument('--baud-rate', type=int, default=115200, help='Baud rate for serial connections (default: 115200)')
    
    # ZMQ options
    parser.add_argument('--zmq-endpoints', nargs='+', help='ZMQ endpoints to connect to (format: tcp://ip:port)')
    
    # Webhook options
    parser.add_argument('--webhook-url', help='Webhook URL to send detection events to')
    
    # Output options
    parser.add_argument('--output-dir', help='Directory to store output files (default: current directory)')
    parser.add_argument('--notifications', action='store_true', help='Enable desktop notifications for new detections')
    
    # General options
    parser.add_argument('--stale-threshold', type=int, default=1, help='Minutes after which a detection is considered stale (default: 1)')
    parser.add_argument('--status-interval', type=int, default=60, help='Interval in seconds between status updates (default: 60)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], default='INFO', help='Log level (default: INFO)')
    
    # Parse arguments
    args = parser.parse_args()
    
    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    
    # Set global BAUD_RATE
    global BAUD_RATE
    if args.baud_rate:
        BAUD_RATE = args.baud_rate
        
    # Check if at least one input method is provided
    if not args.serial_ports and not args.zmq_endpoints:
        parser.error("At least one input method (--serial-ports or --zmq-endpoints) must be provided")
        
    # Initialize and run the application
    app = MeshMapper(args)
    app.run()
        
        
if __name__ == '__main__':
    main()