import xbmcaddon
import xbmc

ADDON = xbmcaddon.Addon()

def log(message):
    xbmc.log(f"[Screen Rec.] {message}", xbmc.LOGINFO)
