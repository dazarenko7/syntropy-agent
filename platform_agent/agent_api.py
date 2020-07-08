import logging
import threading
import os

from platform_agent.lib.get_info import gather_initial_info
from platform_agent.wireguard import WgConfException, WgConf, WireguardPeerWatcher
from platform_agent.docker_api.docker_api import DockerNetworkWatcher
from platform_agent.network.dummy_watcher import DummyNetworkWatcher
from platform_agent.executors.wg_exec import WgExecutor
from platform_agent.network.network_info import BWDataCollect
from platform_agent.network.autoping import AutopingClient

logger = logging.getLogger()


class AgentApi:

    def __init__(self, runner, prod_mode=True):
        self.runner = runner
        self.wg_peers = None
        self.autoping = None
        self.wgconf = WgConf()
        self.wg_executor = WgExecutor(self.runner)
        self.bw_data_collector = BWDataCollect(self.runner)
        if prod_mode:
            threading.Thread(target=self.wg_executor.run).start()
            threading.Thread(target=self.bw_data_collector.run).start()
        if os.environ.get("NOIA_NETWORK_API", '').lower() == "docker" and prod_mode:
            self.network_watcher = DockerNetworkWatcher(self.runner).start()
        if os.environ.get("NOIA_NETWORK_API", '').lower() == "dummy" and prod_mode:
            self.network_watcher = DummyNetworkWatcher(self.runner).start()
        # self.rerouting = Rerouting().start()

    def call(self, type, data, request_id):
        result = None
        try:
            if hasattr(self, type):
                logger.info(f"[AGENT_API] Calling agent api {data}")
                if not isinstance(data, (dict, list)):
                    logger.error('[AGENT_API] data should be "DICT" type')
                    result = {'error': "BAD REQUEST"}
                else:
                    fn = getattr(self, type)
                    result = fn(data, request_id=request_id)
        except AttributeError as error:
            logger.warning(error)
            result = {'error': str(error)}
        return result

    def GET_INFO(self, data, **kwargs):
        return gather_initial_info(**data)

    def WG_INFO(self, data, **kwargs):
        if self.wg_peers:
            self.wg_peers.join(timeout=1)
            self.wg_peers = None
        self.wg_peers = WireguardPeerWatcher(self.runner, **data)
        self.wg_peers.start()
        logger.debug(f"[WIREGUARD_PEERS] Enabled | {data}")

    def WG_CONF(self, data, **kwargs):
        self.wg_executor.queue.put({"data": data, "request_id": kwargs['request_id']})
        return False

    def AUTO_PING(self, data, **kwargs):
        if self.autoping:
            self.autoping.join(timeout=1)
            self.autoping = None
        self.autoping = AutopingClient(self.runner, **data)
        self.autoping.start()
        logger.debug(f"[AUTO_PING] Enabled | {data}")
        return False

    def CONFIG_INFO(self, data, **kwargs):
        self.wgconf.clear_interfaces(data.get('vpn', []))
        for vpn_cmd in data.get('vpn', []):
            try:
                fn = getattr(self.wgconf, vpn_cmd['fn'])
                fn(**vpn_cmd['args'])
            except WgConfException as e:
                logger.error(f"[CONFIG_INFO] {str(e)}")
