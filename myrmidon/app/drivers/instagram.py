"""
MYRMIDON — Instagram Mobile Driver
=====================================
Specific Appium driver for Instagram.
"""

import logging
import time
from .mobile_base import BaseMobileDriver
from .pages.instagram_page import InstagramPostPage

logger = logging.getLogger("myrmidon.drivers.instagram")

class InstagramDriver(BaseMobileDriver):
    def __init__(self, agent_id: str, credentials: dict, device_id: str = None):
        super().__init__(agent_id, credentials, device_id=device_id)
        self.options.set_capability("appPackage", "com.instagram.android")
        self.options.set_capability("appActivity", "com.instagram.android.activity.MainTabActivity")

    def execute_comment(self, target_url: str, text: str) -> bool:
        try:
            self.start_session()
            
            page = InstagramPostPage(self.driver)
            
            logger.info("InstagramDriver [%s]: Opening target URL %s", self.agent_id, target_url)
            page.open_post(target_url)
            
            logger.debug("InstagramDriver [%s]: Waiting for comment button...", self.agent_id)
            page.click_comment()
            
            logger.debug("InstagramDriver [%s]: Waiting for input field...", self.agent_id)
            input_field = page.get_comment_input()
            input_field.click()
            
            self.human_type(input_field, text)
            
            logger.debug("InstagramDriver [%s]: Clicking post button...", self.agent_id)
            page.click_post()
            
            logger.info("InstagramDriver [%s]: Successfully posted comment on Instagram.", self.agent_id)
            time.sleep(2) # Wait for UI to settle
            return True
            
        except Exception as e:
            logger.error("InstagramDriver [%s]: Failed to post comment: %s", self.agent_id, e)
            return False
        finally:
            self.close_session()

