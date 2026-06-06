"""
MYRMIDON — YouTube Mobile Driver
=====================================
Specific Appium driver for YouTube.
"""

import logging
import time
from .mobile_base import BaseMobileDriver
from .pages.youtube_page import YouTubeVideoPage

logger = logging.getLogger("myrmidon.drivers.youtube")

class YouTubeDriver(BaseMobileDriver):
    def __init__(self, agent_id: str, credentials: dict):
        super().__init__(agent_id, credentials)
        self.options.set_capability("appPackage", "com.google.android.youtube")
        self.options.set_capability("appActivity", "com.google.android.youtube.HomeActivity")

    def execute_comment(self, target_url: str, text: str) -> bool:
        try:
            self.start_session()
            
            page = YouTubeVideoPage(self.driver)
            
            logger.info("YouTubeDriver [%s]: Opening target URL %s", self.agent_id, target_url)
            page.open_video(target_url)
            
            logger.debug("YouTubeDriver [%s]: Opening comments section...", self.agent_id)
            page.open_comments_section()
            
            logger.debug("YouTubeDriver [%s]: Waiting for input field...", self.agent_id)
            input_field = page.get_comment_input()
            input_field.click()
            
            self.human_type(input_field, text)
            
            logger.debug("YouTubeDriver [%s]: Clicking send button...", self.agent_id)
            page.click_send()
            
            logger.info("YouTubeDriver [%s]: Successfully posted comment on YouTube.", self.agent_id)
            time.sleep(2) # Wait for UI to settle
            return True
            
        except Exception as e:
            logger.error("YouTubeDriver [%s]: Failed to post comment: %s", self.agent_id, e)
            return False
        finally:
            self.close_session()
