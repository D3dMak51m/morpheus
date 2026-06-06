"""
MYRMIDON — Base Mobile Driver
================================
Base class for Appium-driven mobile automation.
"""

import logging
import time
import random
from typing import Optional
from appium import webdriver
from appium.options.common.base import AppiumOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger("myrmidon.drivers.mobile_base")

APPIUM_SERVER_URL = "http://morpheus-appium:4723" # Or whatever the local appium server is

class BaseMobileDriver:
    def __init__(self, agent_id: str, credentials: dict, platform_name: str = "Android"):
        self.agent_id = agent_id
        self.credentials = credentials
        self.driver: Optional[webdriver.Remote] = None
        self.platform_name = platform_name
        self.options = self._build_options()

    def _build_options(self) -> AppiumOptions:
        options = AppiumOptions()
        options.set_capability("platformName", self.platform_name)
        # In a real setup, we'd need automationName (UiAutomator2), deviceName, etc.
        options.set_capability("automationName", "UiAutomator2")
        options.set_capability("deviceName", "emulator-5554") # Default android emulator
        options.set_capability("noReset", True) # Don't wipe app data
        
        from app.proxy_manager import get_active_proxy
        active_proxy = get_active_proxy()
        if active_proxy:
            proxy_caps = active_proxy.to_appium_caps()
            for k, v in proxy_caps.items():
                 options.set_capability(k, v)
                 
        return options

    def start_session(self):
        logger.info("MobileDriver [%s]: Starting Appium session...", self.agent_id)
        try:
             self.driver = webdriver.Remote(APPIUM_SERVER_URL, options=self.options)
             logger.info("MobileDriver [%s]: Session started successfully.", self.agent_id)
        except Exception as e:
             logger.error("MobileDriver [%s]: Failed to start Appium session: %s", self.agent_id, e)
             raise

    def close_session(self):
        if self.driver:
             logger.info("MobileDriver [%s]: Closing Appium session...", self.agent_id)
             self.driver.quit()
             self.driver = None

    def human_type(self, element, text: str):
        """Simulates typing into a mobile element."""
        logger.debug("MobileDriver [%s]: Typing text (%d chars)...", self.agent_id, len(text))
        # Appium send_keys can overwrite existing text if sent char by char
        # Sending the whole string at once is safer and more reliable.
        element.send_keys(text)
        time.sleep(1)
            
    def execute_comment(self, target_url: str, text: str) -> bool:
        """To be implemented by specific platform drivers."""
        raise NotImplementedError
