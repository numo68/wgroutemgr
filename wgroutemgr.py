import os
import sys
import logging
from datetime import datetime
import docker
from pyroute2 import NDB

MIN_PYTHON = (3, 12)  # For os.setns

LABEL_NETWORK = "wgroutemgr.network"
LABEL_NETWORKS = "wgroutemgr.networks"

DEFAULT_NETWORK = "wg-net"


class WGRouteManager:
    def __init__(self) -> None:
        self.processed = {}
        self.client = docker.from_env()
        self.own_container = None
        self.network_container = None
        self.own_nskey = None
        self.wg_net = DEFAULT_NETWORK
        self.wg_net_ipaddr = None
        self.ndb = NDB()

    def setup(self):
        self.check_env()

        self.own_container = self.get_own_container()
        self.network_container = self.get_network_container()

        logging.info(
            "Own container name %s, using network of %s",
            self.own_container.name,
            self.network_container.name,
        )

        self.own_nskey = self.get_net_ns(self.network_container.id)
        if self.own_nskey == "":
            raise RuntimeError(
                f"Cannot get own network namespace for {self.network_container.name}"
            )

        if LABEL_NETWORK in self.own_container.labels:
            self.wg_net = self.own_container.labels[LABEL_NETWORK]

        try:
            self.wg_net_ipaddr = self.client.api.inspect_container(
                self.network_container.id
            )["NetworkSettings"]["Networks"][self.wg_net]["IPAddress"]
            logging.info("Address of %s is %s", self.wg_net, self.wg_net_ipaddr)
        except Exception as ex:
            raise RuntimeError(f"Cannot get IP address of {self.wg_net}") from ex

        # print(self.ndb.routes.summary().format('json'))

    def check_env(self):
        if sys.version_info < MIN_PYTHON:
            raise RuntimeError(
                f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or later is required."
            )

        if os.uname().sysname != "Linux":
            raise RuntimeError(
                f"Linux OS is required, is {os.uname().sysname}, exiting."
            )

        host = self.client.info()["OperatingSystem"]

        if "Docker Desktop" in host:
            raise RuntimeError(f"{host} does not support bind propagation, exiting")

    def get_network_container(self):
        container_id = ""
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as file:
            line = file.readline().strip()
            while line:
                if "/docker/containers/" in line:
                    container_id = line.split("/docker/containers/")[-1]
                    container_id = container_id.split("/")[0]
                    break
                line = file.readline().strip()

        if not container_id:
            raise RuntimeError("Network container id not found")

        return self.client.containers.get(container_id)

    def get_own_container(self):
        # This needs --cgroupns host
        container_id = None
        with open("/proc/self/cgroup", "r", encoding="utf-8") as file:
            line = file.readline().strip()
            while line:
                if "/system.slice/" in line:
                    container_id = line.split("/docker-")[
                        -1
                    ]  # Take only text to the right
                    container_id = container_id.split(".scope")[
                        0
                    ]  # Take only text to the left
                    break
                line = file.readline().strip()

        if not container_id:
            raise RuntimeError(
                "Own container id not found, was it started with cgroupns host?"
            )

        return self.client.containers.get(container_id)

    def on_started(self, cid):
        c = self.client.containers.get(cid)
        if not c.id in self.processed and LABEL_NETWORKS in c.labels:
            networks = c.labels[LABEL_NETWORKS].split(",")
            nskey = self.get_net_ns(c.id)
            if len(networks) > 0 and nskey != "":
                self.handle_routing(c, networks, nskey)

    def on_died(self, cid):
        if cid in self.processed:
            logging.info("Container %s exited", self.processed[id])
            del self.processed[cid]

    def get_net_ns(self, cid):
        info = self.client.api.inspect_container(cid)
        nskey = info["NetworkSettings"]["SandboxKey"]
        return nskey

    def handle_routing(self, c, networks, nskey):
        logging.info("Setting routing for container %s, networks %s", c.name, networks)
        self.processed[c.id] = c.name
        fd = os.open(nskey, os.O_RDONLY)
        os.setns(fd, os.CLONE_NEWNET)
        os.close(fd)

        fd = os.open(self.own_nskey, os.O_RDONLY)
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
                if e["Type"] == "container":
                    cid = e["id"]
                    if e["Action"] == "start":
                        self.on_started(cid)
                    elif e["Action"] == "die":
                        self.on_died(cid)
                    elif e["Action"] == "kill" and cid == self.own_container.id:
                        break
            except Exception as ex:
                logging.error(ex)

        logging.info("Stopping")


logging.basicConfig(level=logging.INFO)

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
