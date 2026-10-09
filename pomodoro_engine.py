import threading
import time

class PomodoroEngine:
    def __init__(self, wiz_bridge):
        self.wiz = wiz_bridge
        self._thread = None
        self._stop_event = threading.Event()
        
        self.active_ips = []
        self.state = "inactive"  # 'inactive', 'focus', 'warning', 'break'
        self.time_remaining = 0  # in seconds
        
        # Configurations
        self.focus_duration = 25 * 60
        self.warning_threshold = 1 * 60
        self.break_duration = 5 * 60

    def start(self, ips: list[str]):
        """Starts the Pomodoro flow on the given IPs."""
        self.stop()
        self.active_ips = ips
        self._stop_event.clear()
        
        self.state = "focus"
        self.time_remaining = self.focus_duration
        
        # Apply Focus White instantly
        for ip in self.active_ips:
            self.wiz.set_white(ip, 5500, 100)
            
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stops the Pomodoro flow."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.state = "inactive"
        self.time_remaining = 0
        self.active_ips = []

    def get_status(self) -> dict:
        return {
            "state": self.state,
            "time_remaining": self.time_remaining,
            "active_ips": self.active_ips
        }

    def _loop(self):
        """Main tick loop for the timer."""
        # Loop every 1 second
        while not self._stop_event.wait(1.0):
            if self.time_remaining > 0:
                self.time_remaining -= 1
                
                # Check for state transitions
                if self.state == "focus" and self.time_remaining == self.warning_threshold:
                    self.state = "warning"
                    # Soft fade to 50% brightness over 5 seconds to warn user
                    for ip in self.active_ips:
                        self.wiz._start_fade(ip, {"state": True, "temp": 5500, "dimming": 50})
                        
                elif self.state == "warning" and self.time_remaining == 0:
                    # Transition to Break
                    self.state = "break"
                    self.time_remaining = self.break_duration
                    # Ocean/Forest vibes (Cyan/Green)
                    for ip in self.active_ips:
                        self.wiz.set_color(ip, 0, 200, 255, 60, instant=False)
                        
                elif self.state == "break" and self.time_remaining == 0:
                    # End of Pomodoro session
                    self.state = "inactive"
                    self.time_remaining = 0
                    # Return to standard warm white as a neutral finish
                    for ip in self.active_ips:
                        self.wiz.set_white(ip, 3000, 100)
                    break
