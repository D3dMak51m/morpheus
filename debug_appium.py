from appium import webdriver
from appium.options.common.base import AppiumOptions
import time

options = AppiumOptions()
options.set_capability('platformName', 'Android')
options.set_capability('automationName', 'UiAutomator2')
options.set_capability('appPackage', 'com.instagram.android')
options.set_capability('appActivity', 'com.instagram.android.activity.MainTabActivity')
options.set_capability('noReset', True)

try:
    print("Connecting to Appium...")
    driver = webdriver.Remote('http://morpheus-appium:4723', options=options)
    print("Connected! Executing deep link...")
    driver.execute_script('mobile: deepLink', {'url': 'https://www.instagram.com/p/DKOgCPpsSl1PwpjLA21ZcKgzdNqgDItmU6fZHk0/', 'package': 'com.instagram.android'})
    
    print("Waiting 10 seconds for post to load...")
    time.sleep(10)
    
    source = driver.page_source
    print("Got page source. Saving to /tmp/page_source.xml...")
    with open('/tmp/page_source.xml', 'w') as f:
        f.write(source)
    
    print("Done!")
    driver.quit()
except Exception as e:
    print(f"Error: {e}")
