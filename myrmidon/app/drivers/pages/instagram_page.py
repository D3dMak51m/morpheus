from selenium.webdriver.common.by import By
from .base_page import BasePage

class InstagramPostPage(BasePage):
    # Instagram uses "Комментарий" on Russian UI, and class is android.widget.Button
    COMMENT_BUTTON = (By.XPATH, "//*[@content-desc='Comment' or @content-desc='Комментарий' or @resource-id='com.instagram.android:id/row_feed_button_comment']")
    # Once comments open, there is only one text input field
    COMMENT_INPUT = (By.XPATH, "//*[@class='android.widget.EditText' or @class='android.widget.AutoCompleteTextView' or contains(@resource-id, 'layout_comment_thread_edittext')]")
    POST_BUTTON = (By.XPATH, "//*[@text='Post' or @text='Опубликовать' or contains(@content-desc, 'Post') or contains(@content-desc, 'Опубликовать') or contains(@content-desc, 'Отправить') or contains(@content-desc, 'Send') or contains(@resource-id, 'layout_comment_thread_post_button')]")
    
    def open_post(self, target_url: str):
        # Using Android native deep linking to ensure it opens in the Instagram app
        self.driver.execute_script('mobile: deepLink', {
            'url': target_url,
            'package': 'com.instagram.android'
        })

        
    def click_comment(self):
        self.click_element(self.COMMENT_BUTTON)
        
    def get_comment_input(self):
        return self.find_element(self.COMMENT_INPUT)
        
    def click_post(self):
        self.click_element(self.POST_BUTTON)
