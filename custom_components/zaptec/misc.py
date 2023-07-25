"""misc helper stuff"""
import re


def to_under(word) -> str:
    """helper to convert TurnOnThisButton to turn_on_this_button."""
    # Ripped from inflection
    word = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", word)
    word = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", word)
    word = word.replace("-", "_")
    return word.lower()


class Redactor:
    """ Class to handle redaction of sensitive data. """

    # Data fields that must be redacted from the output
    REDACT_KEYS = [
        "Address", "City", "Latitude", "Longitude", "ZipCode",
        "Pin", "SerialNo",
        "Id", "CircuitId", "DeviceId", "InstallationId", "MID", "ChargerId",
        "Name", "InstallationName",
        "SignedMeterValueKwh", "SignedMeterValue",
    ]

    # Keys that will be looked up into the observer id dict
    OBS_KEYS = [
        "SettingId", "StateId"
    ]

    # State or settings ids that must be redacted
    REDACT_IDS = [
        "100",  # ???
        "554",  # SignedMeterValue
        "723",  # CompletedSession
        "750",  # NewChargeCard
        "952",  # MacWiFi
        "960",  # LteImsi
        "962",  # LteIccid
        "963",  # LteImei
    ]

    def __init__(self, redacted, acc):
        self.redacted = redacted
        self.acc = acc
        self.redacts = {}

    def redact(self, text, make_new=False):
        ''' Redact the text if it is present in the redacted dict.
            A new redaction is created if make_new is True
        '''
        if not self.redacted:
            return text
        elif text in self.redacts:
            return self.redacts[text]
        elif make_new:
            red = f"<--Redact #{(len(self.redacts) + 1):02d}-->"
            self.redacts[text] = red
            return red
        return text

    def redact_ids_inplace(self, obj):
        need_redact = any(str(obj.get(k)) in self.REDACT_IDS for k in self.OBS_KEYS)
        for k, v in obj.items():
            if k in self.OBS_KEYS and v in self.acc.obs:
                v = f"{v} {self.acc.obs[v]}"
            if need_redact and 'value' in k.lower():
                v = self.redact(v, make_new=True)
            obj[k] = v

    def redact_obj_inplace(self, obj, mode=None):
        ''' Iterate over obj and redact the fields. NOTE! This function
            modifies the argument object in-place.
        '''
        if isinstance(obj, list):
            for k in obj:
                self.redact_obj_inplace(k, mode=mode)
            return
        elif not isinstance(obj, dict):
            return
        if mode == 'ids':
            self.redact_ids_inplace(obj)
        for k, v in obj.items():
            if isinstance(v, (list, dict)):
                self.redact_obj_inplace(v, mode=mode)
                continue
            obj[k] = self.redact(v, make_new=k in self.REDACT_KEYS)
