```bash
sudo apt update
sudo apt install -y git
sudo git clone https://github.com/grimmorium/gpwbot.git
sudo cp -r gpwbot/ /root

sudo chmod +x /root/gpwbot/0.2/RunAgent_google.bat

#(sudo crontab -l 2>/dev/null; echo "*/30 * * * * /root/gpwbot/0.2/RunAgent_google.bat >> /home/pi/gpwbot/RunAgent_google.log 2>&1") | sudo crontab -
#(sudo crontab -l 2>/dev/null; echo "*/30 * * * * /root/gpwbot/0.2/RunAgent_google.bat >> /home/pi/gpwbot/RunAgent_google.log 2>&1") | sudo crontab -
(sudo crontab -l 2>/dev/null; echo "0 19 * * 1-5 /root/gpwbot/0.2/RunAgent_google.bat >> /home/pi/gpwbot/RunAgent_google.log 2>&1") | sudo crontab -
```
