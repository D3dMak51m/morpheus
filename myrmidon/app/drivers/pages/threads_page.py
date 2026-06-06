from selenium.webdriver.common.by import By
from .base_page import BasePage

class ThreadsPostPage(BasePage):
    COMMENT_BUTTON = (By.XPATH, "//android.widget.Button[contains(@content-desc, 'Reply')]")
    COMMENT_INPUT = (By.XPATH, "//android.widget.EditText[contains(@text, 'Reply to')]")
    POST_BUTTON = (By.XPATH, "//android.widget.Button[@text='Post']")
    
    def open_post(self, target_url: str):
        self.driver.get(target_url)
        
    def click_comment(self):
        self.click_element(self.COMMENT_BUTTON)
        
    def get_comment_input(self):
        return self.find_element(self.COMMENT_INPUT)
        
    def click_post(self):
        self.click_element(self.POST_BUTTON)
