import logging
import docker
import os
import sys
import pyroute2
from datetime import datetime

MIN_PYTHON = (3, 12) # For os.setns
LABEL_NETWORKS="wgroutemgr.networks"

processed = dict()

def get_own_container_id():
    containerID = ''
    with open('/proc/self/mountinfo') as file:
        line = file.readline().strip()    
        while line:
            if '/docker/containers/' in line:
                containerID = line.split('/docker/containers/')[-1]     # Take only text to the right
                containerID = containerID.split('/')[0]                 # Take only text to the left
                break
            line = file.readline().strip()
    return containerID

def on_started(client, id):
    c = client.containers.get(id)
    if not c.id in processed and LABEL_NETWORKS in c.labels:
        networks = c.labels[LABEL_NETWORKS].split(',')
        info = client.api.inspect_container(c.id)
        nskey = info['NetworkSettings']['SandboxKey']
        if len(networks) > 0 and nskey != '':
            handle_routing(c, networks, nskey)

def on_died(id):
    if id in processed:
        logging.info("Container {} exited".format(processed[id]))
        del processed[id]

def handle_routing(c, networks, nskey):
    logging.info("Setting routing for container {}, networks {}".format(c.name, networks))
    processed[c.id] = c.name
    fd = os.open(nskey, os.O_RDONLY)
    os.setns(fd, os.CLONE_NEWNET)
    os.close(fd)

def main_loop():
    own_id = get_own_container_id()

    start_time = datetime.now()

    client = docker.from_env()
    host = client.info()['OperatingSystem']

    if 'Docker Desktop' in host:
        logging.error("{} does not support bind propagation, exiting".format(host))
        sys.exit(1)

    containers = client.containers.list()

    for c in containers:
        try:
            on_started(client, c.id)
        except Exception as ex:
            logging.error(ex)

    for e in client.api.events(since=start_time, decode=True):
        try:
            if e['Type'] == 'container':
                id = e['id']
                if e['Action'] == 'start':
                    on_started(client, id)
                elif e['Action'] == 'die':
                    on_died(id)
                elif (e['Action'] == 'kill' and id == own_id):
                    break
        except Exception as ex:
            logging.error(ex)

    logging.info("Stopping")

logging.basicConfig(level = logging.INFO)
logging.info("Starting")

if sys.version_info < MIN_PYTHON:
    logging.error("Python %s.%s or later is required." % MIN_PYTHON)
    sys.exit(1)

if os.uname().sysname != 'Linux':
    logging.error("Linux OS is required, is {}, exiting.".format(os.uname().sysname))
    sys.exit(1)

try:
    main_loop()
except Exception as ex:
    logging.error(ex)
except KeyboardInterrupt:
    logging.info("Exiting on keyboard interrupt")
