import json
import logging
import threading
import time
import queue
import traceback

from platform_agent.lib.ctime import now
from platform_agent.wireguard import WgConfException, WgConf

logger = logging.getLogger()


class WgExecutor(threading.Thread):
    CMD_TYPE = "WG_CONF"

    def __init__(self, client):
        super().__init__()
        self.queue = queue.Queue()
        self.client = client
        self.stop_wg_executor = threading.Event()
        self.daemon = True
        self.wg = None
        self.wgconf = WgConf()

        threading.Thread.__init__(self)

    def get_from_queue(self):
        payloads = {}
        t_end = time.time() + 1  # run for 1 second
        while time.time() < t_end:
            try:
                message = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            request_id = message['request_id']
            payloads[request_id] = []
            data = message['data'] if type(message['data']) == list else [message['data']]
            for payload in data:
                try:
                    payloads[request_id].append(
                        {
                            "fn_name": payload['fn'],
                            "fn_args": payload['args'],
                            "request_id": request_id
                        }
                    )
                except AttributeError as e:
                    logger.warning(e)
                    payloads[request_id].append(
                        {"request_id": request_id, "error": f"{e}"})
        return payloads

    def run(self):

        while True:
            payloads = self.get_from_queue()
            if not payloads:
                continue
            logger.info(f"[WG_EXECUTOR] - Received {payloads}")
            for request_id in list(payloads.keys()):
                try:
                    self.execute_payload(request_id, payloads)
                except:  # noqa Catch all errors and report to controller
                    # Catch all exceptions that not handled
                    logger.info(f"[WG_EXECUTOR] - catched error")
                    self.send_error(request_id)

    def execute_payload(self, request_id, payloads):
        result = {}
        ok = {}
        errors = []
        for payload in payloads[request_id]:
            if payload.get('error'):
                errors.append(payload['error'])
            else:
                try:
                    fn = getattr(self.wgconf, payload['fn_name'])
                    if payload['fn_name'] in ["add_peer"]:
                        threading.Thread(target=self.add_peer, args=(payload, request_id)).start()
                        continue
                    ok.update(
                        {"fn": payload['fn_name'], "data": fn(**payload['fn_args']), "args": payload['fn_args']})
                    logger.debug(f"[WG_EXECUTOR] - ok - {ok}")

                except WgConfException as e:
                    logger.error(f"[WG_EXECUTOR] failed. exception = {str(e)}, data = {payload}")
                    errors.append({payload['fn_name']: str(e), "args": payload['fn_args']})
            response = {
                'id': request_id,
                'executed_at': now(),
                'type': self.CMD_TYPE,
            }
            if errors:
                response.update({'error': errors, 'data': {}})
            elif ok:
                response.update({'data': ok})
            logger.info(f"[WG_EXECUTOR] - Results {result}")

            self.client.send(json.dumps(response))

    def add_peer(self, payload, request_id):
        response = {}
        logger.debug(f"[WG_EXECUTOR] add_peer starting {payload}")
        try:
            response['data'] = self.wgconf.add_peer(**payload['fn_args'])
        except WgConfException as e:
            logger.error(f"[WG_EXECUTOR] failed. exception = {str(e)}, data = {payload}")
            response['error'] = {payload['fn_name']: str(e), "args": payload['fn_args']}
        except:
            # Catch all exceptions that not handled
            logger.info(f"[WG_EXECUTOR] add_peer error {response} - {request_id}")
            self.send_error(request_id)
            return
        logger.info(f"[WG_EXECUTOR] add_peer sending message {response} - {request_id}")
        self.client.send(json.dumps({
            'id': request_id,
            'executed_at': now(),
            'type': self.CMD_TYPE,
            'data': response
        }))

    def join(self, timeout=None):
        self.stop_wg_executor.set()
        super().join(timeout)

    def send_error(self, request_id):
        traceback.print_exc()
        result = {
            'error': {
                'traceback': traceback.format_exc(),
            }
        }
        logger.error(result)
        logger.debug(f"[RUNNER] WG Executor ERROR | {result}")
        if result:
            payload = {
                'id': request_id,
                'executed_at': now(),
                'type': "WG_CONF",
            }
            if isinstance(result, dict) and ('error' in result):
                payload.update(result)
            else:
                payload.update({'data': result})
            payload = json.dumps(payload)
            self.client.send(payload)
