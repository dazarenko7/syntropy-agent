import socket
import base64
import logging
from pathlib import Path

from pyroute2 import IPDB, WireGuard, IPRoute, NetlinkError
from nacl.public import PrivateKey

logger = logging.getLogger()


class WgConfException(Exception):
    pass


class WgConf():

    def __init__(self):
        self.wg = WireGuard()

    def get_wg_keys(self, ifname):

        private_key = Path(f"/etc/wireguard/privatekey-{ifname}")
        public_key = Path(f"/etc/wireguard/publickey-{ifname}")

        if not private_key.is_file() or not public_key.is_file():
            privKey = PrivateKey.generate()
            pubKey = base64.b64encode(bytes(privKey.public_key))
            privKey = base64.b64encode(bytes(privKey))
            base64_privKey = privKey.decode('ascii')
            base64_pubKey = pubKey.decode('ascii')
            private_key.write_text(base64_privKey)
            public_key.write_text(base64_pubKey)

        return public_key.read_text().strip(), private_key.read_text().strip()

    def next_free_port(self, port=1024, max_port=65535):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        while port <= max_port:
            try:
                sock.bind(('', port))
                sock.close()
                return port
            except OSError:
                port += 1
        raise IOError('no free ports')

    def create_interface(self, ifname, internal_ip, listen_port=None):
        logger.info(f"[WG_CONF] - Creating interface {ifname}")
        public_key, private_key = self.get_wg_keys(ifname)

        with IPDB() as ip:
            wg1 = ip.create(kind='wireguard', ifname=ifname)

            wg1.add_ip(internal_ip)
            wg1.up()
            wg1.commit()

        self.wg.set(
            ifname,
            private_key=private_key,
            fwmark=0x1337,
            listen_port=listen_port
        )

        wg_info = dict(self.wg.info(ifname)[0]['attrs'])
        listen_port = wg_info['WGDEVICE_A_LISTEN_PORT']

        return {
            "public_key": public_key,
            "listen_port": listen_port
        }

    def add_peer(self, ifname, public_key, allowed_ips, gw_ipv4, endpoint_ipv4=None, endpoint_port=None):
        peer = {'public_key': public_key,
                'endpoint_addr': endpoint_ipv4,
                'endpoint_port': endpoint_port,
                'persistent_keepalive': 15,
                'allowed_ips': allowed_ips}
        self.wg.set(ifname, peer=peer)
        self.ip_route_add(ifname, allowed_ips, gw_ipv4)
        return

    def remove_peer(self, ifname, public_key, allowed_ips):
        peer = {
            'public_key': public_key,
            'remove': True
            }

        self.wg.set(ifname, peer=peer)
        self.ip_route_del(ifname, allowed_ips)
        return

    def remove_interface(self, ifname):
        with IPDB() as ipdb:
            if ifname not in ipdb.interfaces:
                raise WgConfException(f'[{ifname}] does not exist')
            with ipdb.interfaces[ifname] as i:
                i.remove()
        return

    def ip_route_add(self, ifname, ip_list, gw_ipv4):
        ip_route = IPRoute()
        devices = ip_route.link_lookup(ifname=ifname)
        dev = devices[0]
        for ip in ip_list:
            try:
                ip_route.route('add', dst=ip, gateway=gw_ipv4, oif=dev)
            except NetlinkError as error:
                if error.code != 17:
                    raise
                logger.info(f"[WG_CONF] add route failed [{ip}] - already exists")

    def ip_route_del(self, ifname, ip_list, scope=None):
        ip_route = IPRoute()
        devices = ip_route.link_lookup(ifname=ifname)
        dev = devices[0]
        for ip in ip_list:
            try:
                ip_route.route('del', dst=ip, oif=dev, scope=scope)
            except NetlinkError as error:
                if error.code != 17:
                    raise
                logger.info(f"[WG_CONF] del route failed [{ip}] - does not exist")
