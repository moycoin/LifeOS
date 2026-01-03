#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Life OS v5.3.0 - Home Cybernetics Module"""
import json,re,subprocess,time,threading
from typing import Dict,Optional,Callable
from enum import Enum
try:
    from phue import Bridge
    PHUE_AVAILABLE = True
except ImportError:
    PHUE_AVAILABLE = False
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
class PowerSavingMode(Enum):
    OFF="off";LOW="low";HIGH="high";PICTURE_OFF="pictureOff"
ALLOWED_ROOMS=["リビングルーム","キッチン","廊下","化粧室","トイレ","浴室","寝室","シューズイン"]
class BraviaController:
    PACKAGE_MAP={'spotify':'Spotify','netflix':'Netflix','youtube':'YouTube','amazon':'Prime Video','disney':'Disney+','hulu':'Hulu','abema':'ABEMA','tver':'TVer','nhkplus':'NHK+','dazn':'DAZN','twitch':'Twitch','plex':'Plex','kodi':'Kodi','vlc':'VLC','crunchyroll':'Crunchyroll','funimation':'Funimation','tv.sony':'Live TV','settings':'Settings','launcher':'Home'}
    def __init__(self,ip:str,psk:str):
        self.ip,self.psk,self.base_url=ip,psk,f"http://{ip}/sony"
        self._power_state,self._volume,self._app_name,self._power_saving,self._adb_connected=None,None,None,None,False
    def _request(self,service:str,method:str,params:list=None,version:str="1.0")->Optional[Dict]:
        if not REQUESTS_AVAILABLE:return None
        try:
            r=requests.post(f"{self.base_url}/{service}",json={"method":method,"params":params or[],"id":1,"version":version},headers={"X-Auth-PSK":self.psk,"Content-Type":"application/json"},timeout=3)
            return r.json() if r.status_code==200 else None
        except:return None
    def _get_app_via_adb(self)->Optional[str]:
        try:
            if not self._adb_connected:
                subprocess.run(['adb','connect',f'{self.ip}:5555'],capture_output=True,timeout=2)
                self._adb_connected=True
            result=subprocess.run(['adb','-s',f'{self.ip}:5555','shell','dumpsys','activity','activities'],capture_output=True,text=True,timeout=2)
            if result.returncode!=0:return None
            for line in result.stdout.split('\n'):
                if 'ResumedActivity' in line or 'mResumedActivity' in line:
                    match=re.search(r'u0\s+([^\s/]+)/',line)
                    if match:
                        package=match.group(1).lower()
                        for key,name in self.PACKAGE_MAP.items():
                            if key in package:return name
                        return package.split('.')[-1].title()
            return None
        except:return None
    def get_power_status(self)->bool:
        result=self._request("system","getPowerStatus")
        if result and "result" in result:
            self._power_state=result["result"][0].get("status")=="active"
            return self._power_state
        return False
    def get_volume(self)->int:
        result=self._request("audio","getVolumeInformation")
        if result and "result" in result:
            for item in result["result"][0]:
                if item.get("target")=="speaker":
                    self._volume=item.get("volume",0)
                    return self._volume
        return 0
    def get_playing_content(self)->str:
        result=self._request("avContent","getPlayingContentInfo")
        title=None
        if result:
            if "error" not in result and "result" in result:
                data=result["result"][0]
                title=data.get("title","") or data.get("programTitle","")
        if not title or title=="Unknown":
            adb_result=self._get_app_via_adb()
            if adb_result:title=adb_result
        self._app_name=title if title else "Unknown"
        return self._app_name
    def get_power_saving_mode(self)->str:
        result=self._request("system","getPowerSavingMode")
        if result and "result" in result:
            self._power_saving=result["result"][0].get("mode","off")
            return self._power_saving
        return "off"
    def set_power_saving_mode(self,mode:PowerSavingMode)->bool:
        result=self._request("system","setPowerSavingMode",[{"mode":mode.value}])
        return result is not None and "result" in result
    def set_volume(self,target:int,max_attempts:int=60)->bool:
        target=max(0,min(100,target))
        current=self.get_volume()
        prev_diff=abs(current-target)
        attempts=0
        while current!=target and attempts<max_attempts:
            result=self._request("audio","setAudioVolume",[{"target":"speaker","volume":str(target)}])
            if not result or "result" not in result:return False
            time.sleep(0.4)
            current=self.get_volume()
            curr_diff=abs(current-target)
            attempts+=1
            if curr_diff>prev_diff:break
            prev_diff=curr_diff
        self._volume=current
        return current==target
    def get_status(self)->Dict:
        return {'power':self.get_power_status(),'volume':self.get_volume(),'app':self.get_playing_content(),'power_saving':self.get_power_saving_mode()}
class HueController:
    def __init__(self,ip:str,room_name:str="リビングルーム"):
        self.ip,self.room_name=ip,room_name
        self._bridge:Optional[Bridge]=None
        self._connected=False
    def connect(self)->bool:
        if not PHUE_AVAILABLE:return False
        try:
            self._bridge=Bridge(self.ip)
            self._bridge.connect()
            self._connected=True
            print(f"[Hue] Connected to {self.ip}")
            return True
        except Exception as e:
            print(f"[Hue] Connection failed: {e}")
            self._connected=False
            return False
    def get_room_brightness(self)->float:
        if not self._connected or not self._bridge:return 0.0
        try:
            for gid,info in self._bridge.get_group().items():
                if info.get('name')==self.room_name:
                    action=info.get('action',{})
                    return (action.get('bri',0)/254) if action.get('on') else 0.0
            return 0.0
        except:return 0.0
    def get_all_rooms_status(self)->Dict:
        if not self._connected or not self._bridge:return {}
        try:
            result={}
            for gid,info in self._bridge.get_group().items():
                gid_int=int(gid)
                if gid_int==11 or gid_int>=200:continue
                name=info.get('name','')
                if name not in ALLOWED_ROOMS:continue
                action=info.get('action',{})
                result[name]={'on':action.get('on',False),'bri':action.get('bri',0)/254 if action.get('bri') else 0.0,'gid':gid_int}
            return result
        except:return {}
    def turn_off_all_except_living(self)->int:
        if not self._connected or not self._bridge:return 0
        try:
            groups=self._bridge.get_group()
            count=0
            for gid,info in groups.items():
                gid_int=int(gid)
                if gid_int==11 or gid_int>=200:continue
                name=info.get('name','')
                if name not in ALLOWED_ROOMS:continue
                if name=="リビングルーム":continue
                if info.get('action',{}).get('on',False):
                    print(f"[Hue] OFF: {name} (ID:{gid_int})")
                    self._bridge.set_group(gid_int,'on',False)
                    count+=1
            return count
        except Exception as e:
            print(f"[Hue] Error: {e}")
            return 0
    def get_status(self)->Dict:
        return {'connected':self._connected,'room':self.room_name,'brightness':self.get_room_brightness(),'all_rooms':self.get_all_rooms_status()}
class AmbientSync:
    POLL_INTERVAL=3.0
    ACTIVITY_TIMEOUT=5.0
    DEFAULT_THRESHOLDS={'off':60,'low':20,'high':1}
    DEFAULT_VOLUME_PROFILES={'Spotify':{'enabled':False,'volume':15},'Netflix':{'enabled':False,'volume':20},'YouTube':{'enabled':False,'volume':20},'Prime Video':{'enabled':False,'volume':20}}
    def __init__(self,config:Dict):
        self.config=config
        self.hue=HueController(config.get('hue_ip',''),config.get('hue_room','リビングルーム'))
        self.bravia=BraviaController(config.get('bravia_ip',''),config.get('bravia_psk',''))
        self._enabled=False
        self._running=False
        self._thread:Optional[threading.Thread]=None
        self._last_mode:Optional[PowerSavingMode]=None
        self._last_app:Optional[str]=None
        self._status_callback:Optional[Callable]=None
        self._hue_status:Dict={}
        self._bravia_status:Dict={}
        self._thresholds=config.get('thresholds',self.DEFAULT_THRESHOLDS.copy())
        self._volume_profiles=config.get('volume_profiles',self.DEFAULT_VOLUME_PROFILES.copy())
        self._last_input_time:float=0.0
        self._focus_lighting_enabled=config.get('focus_lighting',False)
    def set_status_callback(self,callback:Callable):self._status_callback=callback
    def set_thresholds(self,off:int,low:int,high:int):
        self._thresholds={'off':off,'low':low,'high':high}
        self.config['thresholds']=self._thresholds
    def get_thresholds(self)->Dict:return self._thresholds
    def set_volume_profiles(self,profiles:Dict):
        self._volume_profiles=profiles
        self.config['volume_profiles']=profiles
    def get_volume_profiles(self)->Dict:return self._volume_profiles
    def update_user_activity(self,is_active:bool):pass
    def set_focus_lighting(self,enabled:bool):
        self._focus_lighting_enabled=enabled
        self.config['focus_lighting']=enabled
        print(f"[Home] Focus Lighting: {'ENABLED' if enabled else 'DISABLED'}")
    def _is_user_active(self)->bool:
        return (time.time()-self._last_input_time)<self.ACTIVITY_TIMEOUT
    def _apply_focus_lighting(self):
        if not self._focus_lighting_enabled:return
        if not self._is_user_active():return
        count=self.hue.turn_off_all_except_living()
        if count>0:print(f"[Home] Focus: {count} rooms turned off")
    def _brightness_to_mode(self,brightness:float)->PowerSavingMode:
        pct=brightness*100
        if pct>self._thresholds.get('off',60):return PowerSavingMode.OFF
        elif pct>self._thresholds.get('low',20):return PowerSavingMode.LOW
        else:return PowerSavingMode.HIGH
    def _check_app_volume(self,current_app:str):
        if current_app==self._last_app:return
        if current_app and current_app!="Unknown":
            profile=self._volume_profiles.get(current_app)
            if profile and profile.get('enabled',False):
                target_vol=profile.get('volume',20)
                if self.bravia.set_volume(target_vol):
                    print(f"[Home] Volume: {current_app} → {target_vol}")
        self._last_app=current_app
    def _sync_loop(self):
        print("[Home] Sync loop started")
        while self._running:
            try:
                self._hue_status=self.hue.get_status()
                self._bravia_status=self.bravia.get_status()
                if self._enabled and self._bravia_status.get('power',False):
                    brightness=self._hue_status.get('brightness',0.0)
                    target_mode=self._brightness_to_mode(brightness)
                    if target_mode!=self._last_mode:
                        if self.bravia.set_power_saving_mode(target_mode):
                            self._last_mode=target_mode
                    current_app=self._bravia_status.get('app','')
                    self._check_app_volume(current_app)
                self._apply_focus_lighting()
                if self._status_callback:self._status_callback(self._hue_status,self._bravia_status)
            except Exception as e:
                print(f"[Home] Sync error: {e}")
            time.sleep(self.POLL_INTERVAL)
    def start(self)->bool:
        if self._running:return True
        if not self.hue.connect():return False
        self._running=True
        self._thread=threading.Thread(target=self._sync_loop,daemon=True)
        self._thread.start()
        print("[Home] Started")
        return True
    def stop(self):
        self._running=False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread=None
        print("[Home] Stopped")
    def set_enabled(self,enabled:bool):self._enabled=enabled
    def is_enabled(self)->bool:return self._enabled
    def is_running(self)->bool:return self._running
    def get_hue_status(self)->Dict:return self._hue_status
    def get_bravia_status(self)->Dict:return self._bravia_status
    def update_config(self,config:Dict):
        self.config=config
        was_running=self._running
        if was_running:self.stop()
        self.hue=HueController(config.get('hue_ip',''),config.get('hue_room','リビングルーム'))
        self.bravia=BraviaController(config.get('bravia_ip',''),config.get('bravia_psk',''))
        if was_running:self.start()
if __name__=="__main__":
    print(f"=== Home Cybernetics v5.3.0 ===")
    print(f"phue:{PHUE_AVAILABLE} requests:{REQUESTS_AVAILABLE}")
