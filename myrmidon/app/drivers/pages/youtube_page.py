from selenium.webdriver.common.by import By
from .base_page import BasePage

class YouTubeVideoPage(BasePage):
    COMMENTS_SECTION = (By.XPATH, "//android.view.ViewGroup[contains(@content-desc, 'Comments')]")
    ADD_COMMENT_INPUT = (By.XPATH, "//android.widget.EditText[contains(@text, 'Add a comment')]")
    SEND_BUTTON = (By.XPATH, "//android.widget.ImageView[contains(@content-desc, 'Send')]")
    
    def open_video(self, target_url: str):
        self.driver.get(target_url)
        
    def open_comments_section(self):
        self.click_element(self.COMMENTS_SECTION)
        
    def get_comment_input(self):
        return self.find_element(self.ADD_COMMENT_INPUT)
        
    def click_send(self):
        self.click_element(self.SEND_BUTTON)
