import logging
from typing import Tuple
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger("myrmidon.drivers.pages")

class BasePage:
    def __init__(self, driver):
        self.driver = driver
        self.timeout = 10
        
    def find_element(self, locator: Tuple[str, str], timeout=None):
        if timeout is None:
            timeout = self.timeout
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located(locator)
        )
        
    def click_element(self, locator: Tuple[str, str], timeout=None):
        if timeout is None:
            timeout = self.timeout
        element = WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable(locator)
        )
        element.click()
        return element
