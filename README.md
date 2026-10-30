```bash
sudo apt update
sudo apt install -y git
sudo git clone https://github.com/grimmorium/gpwbot.git


(sudo crontab -l 2>/dev/null; echo "*/30 * * * * /home/pi/gpwbot/0.2/RunAgent_google.bat") | sudo crontab -
(sudo crontab -l 2>/dev/null; echo "0 19 * * 1-5 /home/pi/gpwbot/0.2/RunAgent_google.bat") | sudo crontab -
```
