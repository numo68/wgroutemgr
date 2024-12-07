# Route containers through a wireguard one

## Introduction

One way to use the wireguard container is to implement an encrypted channel between containers
running on different hosts. While the wireguard itself allows manipulation of its routing tables,
the containers wanting to use such routes usually do not and there is no documentetd way
to inject a route into a container at the creation time.

The `wgroutemgr` is a tool intended to run as a sidecar of a wireguard container, using
its network stack. It is parametrized with a docker network that container is attached
to (default `wg-net`) and determines its IP address on this network to be used as the
gateway to reach the other side of the tunnel.

The client containers are attached to the same network and are parametrized
with network(s) they want to route through the wireguard. The sidecar monitors
the containers and automatically routes those networks through the gateway.

The typical situation is described in the following picture

```
 192.168.66.9        192.168.66.129                  192.168.67.129       192.168.67.144
   ---------          -------------                  -------------          ---------
  |         | wg-net |             |                |             | wg-net |         |
  |  Cont1  |--------|  wireguard  |=== Internet ===|  wireguard  |--------|  Cont2  |
  |         |        |             |                |             |        |         |
   ---------          -------------                  -------------          ---------
                            |                              |
                      -------------                  -------------
                     |             |                |             |
                     |  wgroutemgr |                | wgroutemgr  |
                     |             |                |             |
                      -------------                  -------------
```

The wireguard containers can easily communicate with each other and on their
respective wg-net networks. For the Cont1 to reach Cont2 it however has to know that
192.168.67.0/24 is reachable through 192.168.66.129.

## Parametrization

The `wgroutemgr` uses the container labels to get the needed parametrization it cannot
acquire autimatically.

### wgroutemgr

`wgroutemgr.network`
: Defines the docker network to use for the routing. The wireguard's address on this network is the gateway. Defaults to `wg-net`.

### Clients

`wgroutemgr.networks` : A comma-separated list of networks in the CIDR notation to route through the gateway.

## Example

A dovecot running on a 192.168.67.0/24 network needs to be reachable through a traefik proxy coming from 192.168.66.0/24
behind a wireguard tunnel.

### Wireguard

```yaml
services:
  wireguard:
    image: lscr.io/linuxserver/wireguard:latest
    ...

  routemgr:
    image: numo68/wgroutemgr
    cap_add:
      - NET_ADMIN
      - SYS_ADMIN
    cgroup: host
    network_mode: service:wireguard
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/run/docker/netns:/var/run/docker/netns:ro,slave
    labels:
      - wgroutemgr.network=wg-net
    restart: unless-stopped

networks:
  wg-net:
    name: wg-net
    external: true
```

### Dovecot

```yaml
services:
  dovecot:
    image: dovecot/dovecot:latest
    ...
    labels:
      - "wgroutemgr.networks=192.168.66.0/24"
    networks:
      wg-net:
        ipv4_address: "192.168.67.144"
    restart: unless-stopped

...

networks:
  wg-net:
    name: wg-net
    external: true
```

### Log

```log
routemgr-1   | INFO:root:Own container name wireguard-routemgr-1, using network of wireguard-wireguard-1
routemgr-1   | INFO:root:Address on wg-net is 192.168.67.129
routemgr-1   | INFO:root:Starting processing
routemgr-1   | INFO:root:Setting routing for container dovecot-dovecot-1
routemgr-1   | INFO:root:Setting route to 192.168.66.0/24 via 192.168.67.129
```

## Caveats

The docker infrastructure has no documented way to introspect the own container from within.
Most of the solutions found on the internet are not foolproof; either they expect cgroups v1,
or fail if the container uses another container's networking, or have other gotchas.

The `wgroutemgr` uses undocumented functionality to determine
- the own container identifier (to read the labels)
- the wireguard container identifier (to get the IP address on the docker network)
- the network namespace of the client containers to manipulate their routing tables

These hacks can stop working at any time either the docker or the underlying OS changes.

At the moment the following prerequisites have to be met
- only Linux is supported
- the Docker Desktop is not supported due to the missing bind propagation (slave mount) on Mac
- the container needs to run with --cgroupns host
- SYS_ADMIN and NET_ADMIN capabilities are needed
- the client containers must not use the networking stack of another container;
  if this is the case, the labels need to be set on the latter
- docker socket needs to be mounted
- docker namespace directory must be mounted as a slave mount

## Source

https://github.com/numo68/wgroutemgr
