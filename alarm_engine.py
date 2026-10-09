import threading
import time
import datetime

class AlarmEngine:
    def __init__(self, wiz_bridge):
        self.wiz = wiz_bridge
        self._thread = None
        self._stop_event = threading.Event()
        
        self.active_ips = []
        self.alarm_time_str = None  # Format: "HH:MM" (e.g. "07:30")
        self.alarm_active = False

    def set_alarm(self, time_str: str, ips: list[str]):
        """Sets the alarm time and target lights."""
        self.alarm_time_str = time_str
        self.active_ips = ips
        self.alarm_active = True
        
        if not self._thread or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def clear_alarm(self):
        """Cancels the current alarm."""
        self.alarm_active = False
        self.alarm_time_str = None
        self.active_ips = []

    def get_status(self) -> dict:
        return {
            "alarm_active": self.alarm_active,
            "alarm_time": self.alarm_time_str,
            "active_ips": self.active_ips
        }

    def _loop(self):
        """Background loop to check the time."""
        has_triggered = False
        
        while not self._stop_event.wait(5.0):
            if not self.alarm_active or not self.alarm_time_str:
                has_triggered = False
                continue
                
            try:
                now = datetime.datetime.now()
                # Parse target time
                h, m = map(int, self.alarm_time_str.split(':'))
                
                # Create a datetime object for the alarm time today
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                
                # If target is in the past for today, it's tomorrow
                if target < now:
                    target = target + datetime.timedelta(days=1)
                    
                # Time until alarm
                time_left = (target - now).total_seconds()
                
                # Trigger sunrise exactly 30 minutes (1800 seconds) before the alarm
                # Allow a small window (e.g. 10 seconds) to catch it
                if 1790 <= time_left <= 1800 and not has_triggered:
                    has_triggered = True
                    # Start a 30-minute crossfade from deep red (dim=1) to bright daylight (dim=100)
                    for ip in self.active_ips:
                        # Turn on at lowest red immediately
                        self.wiz.set_color(ip, 255, 0, 0, 1, instant=True)
                    
                    time.sleep(1) # Let the bulb turn on
                    
                    for ip in self.active_ips:
                        # Start fading to bright white over 1800 seconds (30 mins) with 300 steps (every 6s)
                        target_state = {
                            "state": True,
                            "r": 255,
                            "g": 255,
                            "b": 255,
                            "dimming": 100
                        }
                        self.wiz._start_fade(ip, target_state, duration=1800, steps=300)
                
                # Reset the trigger flag once the alarm time has passed so it arms for tomorrow
                if time_left < 0 or time_left > 1850:
                    has_triggered = False
                    
            except Exception as e:
                print("Alarm engine error:", e)
