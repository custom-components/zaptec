# pylint: disable=C0116


import asyncio
import json
import logging
import re
from concurrent.futures import CancelledError
from functools import partial
from pprint import pformat
import aiohttp
import async_timeout

_LOGGER = logging.getLogger(__name__)

# negative lookahead regex for something
# that looks like json.
jsonish = re.compile(b"(?!.({.+})){.+}")


"""
stuff are missing from the api docs compared to what the portal uses.
circuits/{self.id}/live
circuits/{self.id}/
https://api.zaptec.com/api/dashboard/activechargersforowner?limit=250
/dashbord
signalr is used by the website.
"""

# to Support running this as a script.
if __name__ == "__main__":
    from const import API_URL, CONST_URL, TOKEN_URL
    from misc import to_under, Redactor

    # remove me later
    logging.basicConfig(level=logging.DEBUG)

else:
    from .const import API_URL, CONST_URL, TOKEN_URL
    from .misc import to_under, Redactor


class AuthorizationFailedException(Exception):
    pass


# should be a static method of account
async def _update_remaps() -> None:
    wanted = ["Observations"]
    obs = {}
    async with aiohttp.request("GET", CONST_URL) as resp:
        if resp.status == 200:
            data = await resp.json()
            for k, v in data.items():
                if k in wanted:
                    obs.update(v)
                    # Add names.
                    obs.update({value: key for key, value in v.items()})

        _LOGGER.debug("Update remaps")
        return obs


class ZapBase:
    def __init__(self, data, account):
        self._account = account
        self._data = data
        self._attrs = {}

    def set_attributes(self, data=None):
        if data is None:
            data = self._data
        # _LOGGER.debug("ZapBase set_attributes %s", str(data).encode("utf-8"))

        if not isinstance(data, list):
            new_data = [data]
        else:
            new_data = data

        # known stateid that does not exist in remap
        # have emailed zaptec.
        missing_from_docs = [806]

        for stuff in new_data:
            if "StateId" in stuff:
                obs_key = self._account.obs.get(stuff["StateId"])
                if obs_key is None:
                    if stuff["StateId"] in missing_from_docs:
                        continue
                    _LOGGER.info(
                        "Couldnt find a remap string for %s report it %s",
                        stuff["StateId"],
                        stuff,
                    )
                    continue
                stuff = {obs_key: stuff.get("ValueAsString")}

            for key, value in stuff.items():
                new_key = to_under(key)
                self._attrs[new_key] = value
                _LOGGER.debug(
                    "Setting attribute %s value %s on %s",
                    new_key,
                    value,
                    self.__class__.__name__,
                )

    def __getattr__(self, key):
        try:
            return self._attrs[to_under(key)]
        except KeyError:
            raise


class Circuit(ZapBase):
    """Represents a circuits"""

    def __init__(self, data, account):
        super().__init__(data, account)
        self._chargers = []

        self.set_attributes()

    async def get_chargers(self):
        chargers = []
        for item in self._data["Chargers"]:
            data = await self._account.charger(item["Id"])
            c = Charger(data, self._account)
            if item["Id"] not in self._account.map:
                self._account.map[item["Id"]] = c
            chargers.append(c)
        self.chargers = chargers
        return chargers

    async def state(self):
        data = await self._account._request(f"circuits/{self.id}/")
        self.set_attributes(data)


class Installation(ZapBase):
    """This class represents an Installation"""

    def __init__(self, data, account):
        super().__init__(data, account)
        # fill out stuff here.
        self.connection_details = None
        self.circuits = []

        self._stream_task = None
        self._stream_receiver = None
        self.set_attributes()

    async def build(self):
        data = await self._account.hierarchy(self.id)

        for item in data["Circuits"]:
            c = Circuit(item, self._account)
            if item["Id"] not in self._account.map:
                self._account.map[item["Id"]] = c
            await c.get_chargers()
            self.circuits.append(c)

    async def state(self):
        data = await self._account.installation(self.id)
        self.set_attributes(data)

    async def limit_current(self, **kwargs):
        """Set a limit now how many amps the installation can use

        Use availableCurrent for 3phase
        use just select the phase you want to use.


        """
        total = "availableCurrent"
        phases = [
            "availableCurrentPhase1",
            "availableCurrentPhase2",
            "availableCurrentPhase3",
        ]

        # If any of the phases are present and not None, remove the total field.
        if any(k and v is not None
               for k, v in kwargs.items()
               if k in phases
               ):
            kwargs.pop(total, None)

        return await self._account._request(
            f"installation/{self.id}/update", method="post", data=kwargs
        )

    async def live_stream_connection_details(self):
        data = await self._account._request(
            f"installation/{self.id}/messagingConnectionDetails"
        )
        self.connection_details = data
        return data

    async def stream(self, cb=None):
        """Kickoff the steam in the background."""
        try:
            from azure.servicebus.aio import ServiceBusClient
            from azure.servicebus.exceptions import ServiceBusError
        except ImportError:
            _LOGGER.debug("Azure Service bus is not available. Resolving to polling")
            # https://github.com/custom-components/zaptec/issues
            return

        await self.cancel_stream()
        self._stream_task = asyncio.create_task(self._stream(cb=cb))

    async def _stream(self, cb=None):
        try:
            from azure.servicebus.aio import ServiceBusClient
            from azure.servicebus.exceptions import ServiceBusError
        except ImportError:
            _LOGGER.debug("Azure Service bus is not available. Resolving to polling")
            # https://github.com/custom-components/zaptec/issues
            return

        conf = await self.live_stream_connection_details()
        # Check if we can use it.
        if any(True for i in ["Password", "Username", "Host"] if conf.get(i) == ""):
            _LOGGER.warning(
                "Cant enable live update using the servicebus, enable it in the zaptec portal"
            )
            return

        constr = f'Endpoint=sb://{conf["Host"]}/;SharedAccessKeyName={conf["Username"]};SharedAccessKey={conf["Password"]}'
        servicebus_client = ServiceBusClient.from_connection_string(conn_str=constr)
        _LOGGER.debug("Connecting to servicebus using %s", constr)

        # To be removed.
        seen = set()
        obs_values = set(self._account.obs.values())
        obs = self._account.obs

        try:
            self._stream_receiver = None
            async with servicebus_client:
                receiver = servicebus_client.get_subscription_receiver(
                    topic_name=conf["Topic"], subscription_name=conf["Subscription"]
                )
                # Store the receiver in order to close it and cancel this stream
                self._stream_receiver = receiver
                async with receiver:
                    async for msg in receiver:
                        await asyncio.sleep(0)
                        _LOGGER.debug("Got a message from the servicebus")
                        # pretty sure there should be some other
                        # better way to handle this but it will have to do
                        # for now. # FIXME
                        body = b"".join(msg.body)
                        # body = "".join(body.decode(enc))
                        _LOGGER.debug("body was %s", body)
                        found = jsonish.search(body)
                        if found:
                            found = found.group()
                            _LOGGER.debug("found %s", found)
                            json_result = json.loads(found.decode("utf-8"))

                            _LOGGER.debug("%s", found)

                            # Add this should be removed later, only added.
                            std = json_result.get("StateId")
                            name = obs.get(std)

                            if std and std not in seen:
                                seen.add(json_result.get("StateId"))
                                _LOGGER.debug(
                                    "Added %s %s to seen got %s of %s",
                                    std,
                                    to_under(name),
                                    len(seen),
                                    len(obs_values),
                                )
                                _LOGGER.debug(
                                    "Have ids: %s", ", ".join([str(i) for i in seen])
                                )
                                _LOGGER.debug(
                                    "Have names: %s",
                                    ", ".join([to_under(obs.get(i, "")) for i in seen]),
                                )
                                _LOGGER.debug(
                                    "Missing %s", ", ".join([str(i) for i in obs_values])
                                )

                            # Execute the callback.
                            if cb:
                                await cb(json_result)

                        else:
                            _LOGGER.debug(
                                "Couldn't extract the json from the message body, %s", body
                            )

                        # remove the msg from the "queue"
                        await receiver.complete_message(msg)
        finally:
            # To ensure its not set if not active
            self._stream_receiver = None

    async def cancel_stream(self):
        try:
            from azure.servicebus.exceptions import ServiceBusError
        except ImportError:
            return

        if self._stream_task is not None:
            try:
                if self._stream_receiver is not None:
                    await self._stream_receiver.close()
                self._stream_task.cancel()
                await self._stream_task
                _LOGGER.debug("Canceled stream")
            except (ServiceBusError, CancelledError):
                pass
                # this will still raise a exception, I think its a 3.7 issue.
                # recheck this when the i have updated to 3.9

            self._stream_task = None


class Account:
    """This class represent an zaptec account"""

    def __init__(self, username, password, client=None):
        self._username = username
        self._password = password
        self._client = client
        self._token_info = {}
        self._access_token = None
        self.installs = []
        self.stand_alone_chargers = []
        # Map using the id for lookupss
        self.map = {}
        self.obs = {}
        if client is None:
            self._client = aiohttp.ClientSession()
        self.is_built = False

    def update(self, data):
        """update for the stream. Note build has to called first."""
        if not isinstance(data, list):
            data = [data]

        for d in data:
            # this might be some other ids to, check it #TODO
            cls_id = d.pop("ChargerId", None)
            if cls_id is not None:
                klass = self.map.get(cls_id)
                if klass:
                    klass.set_attributes(d)

        self.is_built = True

    @staticmethod
    async def check_login(username, password):
        p = {
            "username": username,
            "password": password,
            "grant_type": "password",
        }
        try:
            async with aiohttp.request("POST", TOKEN_URL, data=p) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return True
                else:
                    raise AuthorizationFailedException
        except aiohttp.ClientConnectorError as err:
            _LOGGER.exception("Bad things happend while trying to authenticate :(")
            raise

        return False

    async def _refresh_token(self):
        # So for some reason they used grant_type password..
        # what the point with oauth then? Anyway this is valid for 24 hour
        p = {
            "username": self._username,
            "password": self._password,
            "grant_type": "password",
        }
        async with aiohttp.request("POST", TOKEN_URL, data=p) as resp:
            if resp.status == 200:
                data = await resp.json()
                # The data includes the time the access token expires
                # atm we just ignore it and refresh token when needed.
                self._token_info.update(data)
                self._access_token = data.get("access_token")
            else:
                _LOGGER.debug("Failed to refresh token, check your credentials.")

    async def _request(self, url, method="get", data=None):
        header = {
            "Authorization": "Bearer %s" % self._access_token,
            "Accept": "application/json",
        }
        full_url = API_URL + url
        try:
            with async_timeout.timeout(30):
                call = getattr(self._client, method)
                if data is not None and method == "post":
                    call = partial(call, json=data)
                async with call(full_url, headers=header) as resp:
                    if resp.status == 401:  # Unauthorized
                        await self._refresh_token()
                        return await self._request(url)
                    elif resp.status == 204:  # No content
                        content = await resp.read()
                        return content
                    elif resp.status == 200:  # OK
                        json_result = await resp.json(content_type=None)
                        return json_result
                    else:
                        _LOGGER.error("Could not get info from %s: %s", full_url, resp)
                        return None

        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Could not get info from %s: %s", full_url, err)

    async def hierarchy(self, installation_id):
        return await self._request(f"installation/{installation_id}/hierarchy")

    async def installations(self):
        data = await self._request("installation")
        return data

    async def installation(self, installation_id):
        data = await self._request(f"installation/{installation_id}")
        return data

    async def charger(self, charger_id):
        data = await self._request(f"chargers/{charger_id}")
        return data

    async def charger_firmware(self, installation_id):
        data = await self._request(f"chargerFirmware/installation/{installation_id}")
        return data

    async def chargers(self):
        charg = await self._request("chargers")
        return [Charger(chrg, self) for chrg in charg.get("Data", []) if chrg]

    async def build(self):
        """Make the python interface."""
        if not len(self.obs):
            self.obs = await _update_remaps()
        installations = await self.installations()

        cls_installs = []
        for data in installations["Data"]:
            install_data = await self.installation(data["Id"])
            I = Installation(install_data, self)

            self.map[data["Id"]] = I
            await I.build()
            cls_installs.append(I)

        self.installs = cls_installs

        so_chargers = await self.chargers()
        for charger in so_chargers:
            if charger.id not in self.map:
                self.map[charger.id] = charger
        self.stand_alone_chargers = so_chargers

    async def data_dump(self, redacted=True):
        """ Debug API data dump """

        # Helper to redact the output data
        red = Redactor(redacted, self)

        def gen_text(text, obj, mode=None):
            if mode != 'noredact':
                red.redact_obj_inplace(obj, mode=mode)
            return f"\n{text}\n{'='*len(text)}\n{pformat(obj, compact=False, width=200)}\n"

        async def req(url):
            try:
                return await self._request(url)
            except Exception as err:
                return {"FAILED": str(err)}

        # Fetch from API and generate output
        # ----------------------------------

        installations = await req("installation")
        installation_ids = [inst['Id'] for inst in installations.get('Data',[])]
        yield gen_text("installation", installations)

        chargers = await req("chargers")
        charger_ids = [charger['Id'] for charger in chargers.get('Data',[])]
        yield gen_text("chargers", chargers)

        circuit_ids = []
        charger_in_circuits_ids = []
        for inst_id in installation_ids:
            hierarchy = await req(f"installation/{inst_id}/hierarchy")

            for circuit in hierarchy.get('Circuits', []):
                circuit_ids.append(circuit['Id'])
                for charger in circuit.get('Chargers', []):
                    charger_in_circuits_ids.append(charger['Id'])

            yield gen_text(f"installation/{red.redact(inst_id)}/hierarchy", hierarchy)

            installation = await req(f"installation/{inst_id}")
            yield gen_text(f"installation/{red.redact(inst_id)}", installation)

        for circ_id in circuit_ids:
            circuit = await req(f"circuits/{circ_id}")
            yield gen_text(f"circuits/{red.redact(circ_id)}", circuit)

        for charger_id in set([*charger_ids, *charger_in_circuits_ids]):
            charger = await req(f"chargers/{charger_id}")
            yield gen_text(f"chargers/{red.redact(charger_id)}", charger)

            state = await req(f"chargers/{charger_id}/state")
            yield gen_text(f"chargers/{red.redact(charger_id)}/state", state, mode='ids')

            settings = await req(f"chargers/{charger_id}/settings")
            yield gen_text(f"chargers/{red.redact(charger_id)}/settings", settings, mode='ids')

        # # Print the redaction map
        # reds = {v: k for k, v in red.redacts.items()}
        # yield gen_text("**REDACTIONS**", reds, mode='noredact')


class Charger(ZapBase):
    """Represents a charger"""

    def __init__(self, data, account):
        super().__init__(data, account)
        self.set_attributes()

    # All methods need to be checked again.
    async def restart_charger(self):
        return await self._send_command(102)

    async def restart_mcu(self):
        return await self._send_command(103)

    async def update_settings(self):
        return await self._send_command(104)

    async def restart_ntp(self):
        return await self._send_command(105)

    async def exit_app_with_code(self):
        return await self._send_command(106)

    async def upgrade_firmware(self):
        return await self._send_command(200)

    async def upgrade_firmware_forced(self):
        return await self._send_command(201)

    async def reset_com_errors(self):
        return await self._send_command(260)

    async def reset_notifications(self):
        return await self._send_command(261)

    async def reset_com_warnings(self):
        return await self._send_command(262)

    async def local_settings(self):
        return await self._send_command(260)

    async def set_plc_npw(self):
        return await self._send_command(320)

    async def set_plc_cocode(self):
        return await self._send_command(321)

    async def set_plc_nmk(self):
        return await self._send_command(322)

    async def set_remote_plc_nmk(self):
        return await self._send_command(323)

    async def set_remote_plc_npw(self):
        return await self._send_command(324)

    async def start_charging(self):
        _LOGGER.debug("Attempting to start charging")
        return await self._send_command(501)

    async def stop_charging(self):
        _LOGGER.debug("Attempting to stops charging")
        return await self._send_command(502)

    async def report_charging_state(self):
        return await self._send_command(503)

    async def set_session_id(self):
        return await self._send_command(504)

    async def set_user_uuid(self):
        return await self._send_command(505)

    #  require firmware > 3.2
    async def stop_pause(self):
        return await self._send_command(506)

    #  require firmware > 3.2
    async def resume_charging(self):
        return await self._send_command(507)

    async def show_granted(self):
        return await self._send_command(601)

    async def show_denied(self):
        return await self._send_command(602)

    async def indicate_app_connect(self):
        return await self._send_command(603)

    async def confirm_charge_card_added(self):
        return await self._send_command(750)

    async def set_authentication_list(self):
        return await self._send_command(751)

    async def debug(self):
        return await self._send_command(800)

    async def get_plc_topology(self):
        return await self._send_command(801)

    async def reset_plc(self):
        return await self._send_command(802)

    async def remote_command(self):
        return await self._send_command(803)

    async def run_grid_test(self):
        return await self._send_command(804)

    async def run_post_production_test(self):
        return await self._send_command(901)

    async def combined_min(self):
        return await self._send_command(10000)

    async def deauthorize_stop(self):
        return await self._send_command(10001)

    async def combined_max(self):
        return await self._send_command(10999)

    async def state(self):
        data = await self._account._request(f"chargers/{self.id}/state")
        # sett_attributes need to be set before any other call.
        self.set_attributes(data)
        # Firmware version is called. SmartMainboardSoftwareApplicationVersion,
        # stateid 908
        # I couldn't find a way to see if it was up to date..
        # maybe remove this later if it dont interest ppl.
        if self.installation_id in self._account.map:
            firmware_info = await self._account.charger_firmware(self.installation_id)
            for fm in firmware_info:
                if fm["ChargerId"] == self.id:
                    fixed = {
                        "current_firmware_version": fm["CurrentVersion"],
                        "available_firmware_version": fm["AvailableVersion"],
                        "firmware_update_to_date": fm["IsUpToDate"],
                    }
                    self.set_attributes(fixed)

    async def live(self):
        # This don't seems to be documented but the portal uses it
        # TODO check what it returns and parse it to attributes
        return await self._account._request("chargers/%s/live" % self.id)

    async def settings(self):
        # TODO check what it returns and parse it to attributes
        return await self._account._request("chargers/%s/settings" % self.id)

    async def _send_command(self, id_):
        cmd = "chargers/%s/SendCommand/%s" % (self.id, id_)
        _LOGGER.debug("Calling %s", cmd)
        return await self._account._request(cmd, method="post")

    async def update(self, data):
        # https://api.zaptec.com/help/index.html#/Charger/post_api_chargers__id__update
        # Not really sure this should be added as ppl might use it wrong
        cmd = f"chargers/{self.id}/update"
        default = {
            # nullable: true
            # Adjustable between 0 and 32A. If charge current is below the charger minimum charge current (usually 6A), no charge current will be allocated.
            "maxChargeCurrent": 0,
            # MaxPhaseinteger($int32) # Enum 1,3
            "maxChargePhases": "",
            # The minimum allocated charge current. If there is not enough current available to provide the
            # chargers minimum current it will not be able to charge.
            # Usually set to match the vehicle minimum current for charging (defaults to 6A)
            "minChargeCurrent": None,
            # Adjustable between 0 and 32A. If offline charge current is below the charger minimum charge current (usually 6A),
            # no charge current will be allocated when offline.
            # Offline current override should only be done in special cases where charging stations should not automatically optimize offline current.
            # In most cases this setting should be set to -1 to allow ZapCloud to optimise offline current. If -1, offline current will be automatically allocated.
            "offlineChargeCurrent": None,
            # Phasesinteger($int32) ENUM
            # 0 = None
            # 1 = Phase_1
            # 2 = Phase_2
            # 4 = Phase_3
            # 7 = All
            "offlineChargePhase": None,
            # nullable
            # The interval in seconds for a charger to report meter values. Defaults to 900 seconds for Pro and 3600 seconds for Go
            "meterValueInterval": None,
        }

        pass

        # return await self._account.request(cmd, data=data, method="post")


if __name__ == "__main__":
    # Just to execute the script manually.
    import os

    async def gogo():
        username = os.environ.get("zaptec_username")
        password = os.environ.get("zaptec_password")
        acc = Account(
            username,
            password,
            client=aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(verify_ssl=False)
            ),
        )
        # Builds the interface.
        await acc.build()

        # async def cb(data):
        #     _LOGGER.info("CB")
        #     print(data)

        # for ins in acc.installs:
        #     for circuit in ins.circuits:
        #         data = await circuit.state()
        #         print(data)
        #     # await ins._stream(cb=cb)

        # for charger in acc.stand_alone_chargers:
        #     data = await charger.state()
        #     print(data)

        # Dump the raw API data
        async for text in acc.data_dump(redacted=False):
            print(text, end='')

        await acc._client.close()

    asyncio.run(gogo())
