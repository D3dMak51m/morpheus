"""
MYRMIDON — Threads Mobile Driver
=====================================
Specific Appium driver for Threads.
"""

import logging
import time
from .mobile_base import BaseMobileDriver
from .pages.threads_page import ThreadsPostPage

logger = logging.getLogger("myrmidon.drivers.threads")

class ThreadsDriver(BaseMobileDriver):
    def __init__(self, agent_id: str, credentials: dict):
        super().__init__(agent_id, credentials)
        self.options.set_capability("appPackage", "com.instagram.barcelona")
        self.options.set_capability("appActivity", "com.instagram.barcelona.mainactivity.BarcelonaActivity")

    def execute_comment(self, target_url: str, text: str) -> bool:
        try:
            self.start_session()
            
            page = ThreadsPostPage(self.driver)
            
            logger.info("ThreadsDriver [%s]: Opening target URL %s", self.agent_id, target_url)
            page.open_post(target_url)
            
            logger.debug("ThreadsDriver [%s]: Waiting for comment button...", self.agent_id)
            page.click_comment()
            
            logger.debug("ThreadsDriver [%s]: Waiting for input field...", self.agent_id)
            input_field = page.get_comment_input()
            input_field.click()
            
            self.human_type(input_field, text)
            
            logger.debug("ThreadsDriver [%s]: Clicking post button...", self.agent_id)
            page.click_post()
            
            logger.info("ThreadsDriver [%s]: Successfully posted comment on Threads.", self.agent_id)
            time.sleep(2) # Wait for UI to settle
            return True
            
        except Exception as e:
            logger.error("ThreadsDriver [%s]: Failed to post comment: %s", self.agent_id, e)
            return False
        finally:
            self.close_session()
