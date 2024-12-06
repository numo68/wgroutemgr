import logging
import docker
import os
import sys
import pyroute2
from datetime import datetime

MIN_PYTHON = (3, 12) # For os.setns
LABEL_NETWORKS="wgroutemgr.networks"

class WGRouteManager:
    def __init__(self) -> None:
        self.processed = dict()
        self.client = docker.from_env()
        self.own_id = None

    def setup(self):
        self.check_env()
        containerID = ''
        with open('/proc/self/mountinfo') as file:
            line = file.readline().strip()    
            while line:
                if '/docker/containers/' in line:
                    containerID = line.split('/docker/containers/')[-1]     # Take only text to the right
                    containerID = containerID.split('/')[0]                 # Take only text to the left
                    break
                line = file.readline().strip()
        self.own_id = containerID

    def check_env(self):
        if sys.version_info < MIN_PYTHON:
            raise RuntimeError(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or later is required.")

        if os.uname().sysname != 'Linux':
            raise RuntimeError(f"Linux OS is required, is {os.uname().sysname}, exiting.")

        host = self.client.info()['OperatingSystem']

        if 'Docker Desktop' in host:
            raise RuntimeError(f"{host} does not support bind propagation, exiting")

    def on_started(self, id):
        c = self.client.containers.get(id)
        if not c.id in self.processed and LABEL_NETWORKS in c.labels:
            networks = c.labels[LABEL_NETWORKS].split(',')
            info = self.client.api.inspect_container(c.id)
            nskey = info['NetworkSettings']['SandboxKey']
            if len(networks) > 0 and nskey != '':
                self.handle_routing(c, networks, nskey)

    def on_died(self, id):
        if id in self.processed:
            logging.info(f"Container {self.processed[id]} exited")
            del self.processed[id]

    def handle_routing(self, c, networks, nskey):
        logging.info(f"Setting routing for container {c.name}, networks {networks}")
        self.processed[c.id] = c.name
        fd = os.open(nskey, os.O_RDONLY)
        os.setns(fd, os.CLONE_NEWNET)
        os.close(fd)

    def loop(self):
        logging.info("Starting processing")

        start_time = datetime.now()
        containers = self.client.containers.list()

        for c in containers:
            try:
                self.on_started(c.id)
            except Exception as ex:
                logging.error(ex)

        for e in self.client.api.events(since=start_time, decode=True):
            try:
                if e['Type'] == 'container':
                    id = e['id']
                    if e['Action'] == 'start':
                        self.on_started(id)
                    elif e['Action'] == 'die':
                        self.on_died(id)
                    elif (e['Action'] == 'kill' and id == self.own_id):
                        break
            except Exception as ex:
                logging.error(ex)

        logging.info("Stopping")


logging.basicConfig(level = logging.INFO)

mgr = WGRouteManager()

try:
    mgr.setup()
    mgr.loop()
except RuntimeError as ex:
    logging.error(ex)
    sys.exit(1)
except Exception as ex:
    logging.error(ex)
except KeyboardInterrupt:
    logging.info("Exiting on keyboard interrupt")
