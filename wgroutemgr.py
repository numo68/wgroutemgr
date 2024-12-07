"""
A tool intended to be used as a sidecar to the wireguard (or other VPN) container.

Containers can declare the networks they want to route via the wireguard
using a label. The sidecar then automatically sets up the necessary route.
"""

import os
import sys
import logging
import ipaddress
from datetime import datetime
import docker
from pyroute2 import NDB, netns

MIN_PYTHON = (3, 12)

LABEL_NETWORK = "wgroutemgr.network"
LABEL_NETWORKS = "wgroutemgr.networks"

DEFAULT_NETWORK = "wg-net"


class WGRouteManager:
    """
    Monitors starting containers connected to the specified network
    and sets up necessary routes
    """

    def __init__(self) -> None:
        """Initialization"""
        self.processed = {}
        self.client = docker.from_env()
        self.own_container = None
        self.network_container = None
        self.wg_net = DEFAULT_NETWORK
        self.wg_net_ipaddr = None

    def setup(self):
        """Initialize the docker environment and get the network information"""
        self.check_env()

        self.own_container = self.get_own_container()
        self.network_container = self.get_network_container()

        logging.info(
            "Own container name %s, using network of %s",
            self.own_container.name,
            self.network_container.name,
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

    def check_env(self):
        """Check the prerequisites

        Raises:
            RuntimeError: The environment is insufficient to run the tool
        """
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

    def get_own_container(self):
        """Gets the own container

        There is no documented way to get the information of an own container, so this
        is a hack that might stop working at any time. Most of the way on the internet
        either need cgroups v1 or use what is actually the identifier of the container
        providing the network stack, which is unusable in case we want to read own labels
        or monitor events to ur container.

        Example:
        0::/system.slice/docker-40bca618699cc2400869a366399a4495c9849d3c0f756fedf198a5b60bd9830d.scope

        For the implement method to work the container needs to be created with
        --cgroupns host.

        Raises:
            RuntimeError: The own container could not be identified

        Returns:
            Container: own docker container
        """
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

    def get_network_container(self):
        """Gets the container providing the networking

        There is no documented way to get the information of the container providing
        the networking, so this is a hack that might stop working at any time.
        It identifies where the information such as /etc/hosts was mounted from,
        expecting that the identifier is path of it.

        Example:
        1356 1234 202:1 /var/lib/docker/containers/23d4e2c47957387137745467eaa48d76fb06c7a47a17424924f7ce82a6244da7/resolv.conf /etc/resolv.conf rw,relatime - ext4 /dev/xvda1 rw,errors=remount-ro

        Raises:
            RuntimeError: The network container could not be identified

        Returns:
            Container: docker container providing the networking
        """
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

    def on_started(self, cid):
        """Runs when a container is started

        If the container requests a routing, its network namespace is determined,
        membership in the network served by this helper verified and the method
        implementing the actual route management is called.

        Args:
            cid (string): Container identifier
        """
        c = self.client.containers.get(cid)
        if not c.id in self.processed and LABEL_NETWORKS in c.labels:
            networks = [
                ipaddress.ip_network(n, strict=True)
                for n in c.labels[LABEL_NETWORKS].split(",")
            ]
            if len(networks) > 0:
                info = self.client.api.inspect_container(cid)
                nskey = info["NetworkSettings"]["SandboxKey"]
                member_networks = info["NetworkSettings"]["Networks"]
                if nskey == "":
                    logging.warning(
                        "Routing requested for %s but no network namespace. --network:container:... is not supported",
                        c.name,
                    )
                elif not self.wg_net in member_networks:
                    logging.warning(
                        "Container %s is not attached to network %s",
                        c.name,
                        self.wg_net,
                    )
                else:
                    self.handle_routing(c, networks, nskey)

    def on_died(self, cid):
        """Runs when a container dies

        Args:
            cid (string): Container identifier
        """
        if cid in self.processed:
            logging.info("Container %s exited", self.processed[cid])
            del self.processed[cid]

    def handle_routing(self, c, networks, nskey):
        """Sets the requested routing

        Sets or changes the routing to the specified destination(s) via
        the IP address of the container implementing the networking.

        Args:
            c (Container): Docker container requesting the routing
            networks (IPv4Network[]): Networks to route through this helper
            nskey (string): Network namespace to set the routing in
        """
        logging.info("Setting routing for container %s", c.name)

        netns.pushns(nskey)
        try:
            ndb = NDB()
            for n in networks:
                set_route = True
                try:
                    check_route = ndb.routes[{"dst": str(n)}]

                    if (
                        check_route["dst"] != str(n.network_address)
                        or check_route["dst_len"] != n.prefixlen
                        or check_route["gateway"] != self.wg_net_ipaddr
                    ):
                        logging.info(
                            "Removing existing route to %s via %s",
                            n,
                            check_route["gateway"],
                        )
                        check_route.remove().commit()
                    else:
                        logging.info("Route to %s already set", n)
                        set_route = False
                except KeyError:
                    pass

                if set_route:
                    logging.info("Setting route to %s via %s", n, self.wg_net_ipaddr)
                    ndb.routes.create(dst=str(n), gateway=self.wg_net_ipaddr).commit()

            ndb.close()
            self.processed[c.id] = c.name
        finally:
            netns.popns()

    def loop(self):
        """Main loop

        Handles containers existing at the moment of the start, then listens for
        the events on the docker sockets and handles new and departing ones.
        """
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
