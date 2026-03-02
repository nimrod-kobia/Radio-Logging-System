import ctypes


ES_AWAYMODE_REQUIRED = 0x00000040
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class PowerManager:
    def __init__(self):
        self.enabled = False

    def enable_keep_awake(self) -> bool:
        try:
            result = ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            )
            self.enabled = bool(result)
            return self.enabled
        except Exception:
            self.enabled = False
            return False

    def disable_keep_awake(self) -> bool:
        try:
            result = ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            self.enabled = False
            return bool(result)
        except Exception:
            self.enabled = False
            return False
