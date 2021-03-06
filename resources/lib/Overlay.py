#   Copyright (C) 2013 Lunatixz
#
#
# This file is part of PseudoTV.
#
# PseudoTV is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PseudoTV is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PseudoTV.  If not, see <http://www.gnu.org/licenses/>.

import xbmc, xbmcgui, xbmcaddon
import subprocess, os
import time, threading
import datetime
import sys, re
import random, traceback
import urllib
import fanarttv

from fanarttv import *
from Playlist import Playlist
from Globals import *
from Channel import Channel
from EPGWindow import EPGWindow
from ChannelList import ChannelList
from ChannelListThread import ChannelListThread
from FileAccess import FileLock, FileAccess
from Migrate import Migrate
from Downloader import *


class MyPlayer(xbmc.Player):
    def __init__(self):
        xbmc.Player.__init__(self, xbmc.PLAYER_CORE_AUTO)
        self.stopped = False
        self.ignoreNextStop = False

    
    def log(self, msg, level = xbmc.LOGDEBUG):
        log('Player: ' + msg, level)
    
    
    def onPlayBackStopped(self):
        if self.stopped == False:
            self.log('Playback stopped')

            if self.ignoreNextStop == False:
                if self.overlay.sleepTimeValue == 0:
                    self.overlay.sleepTimer = threading.Timer(1, self.overlay.sleepAction)

                self.overlay.background.setVisible(True)
                self.overlay.sleepTimeValue = 1
                self.overlay.startSleepTimer()
                self.stopped = True
            else:
                self.ignoreNextStop = False

# overlay window to catch events and change channels
class TVOverlay(xbmcgui.WindowXMLDialog):

    def __init__(self, *args, **kwargs):
        xbmcgui.WindowXMLDialog.__init__(self, *args, **kwargs)
        self.log('__init__')
        # initialize all variables
        self.channels = []
        self.Player = MyPlayer()
        self.Player.overlay = self
        self.inputChannel = -1
        self.channelLabel = []
        self.lastActionTime = 0
        self.actionSemaphore = threading.BoundedSemaphore()
        self.channelThread = ChannelListThread()
        self.channelThread.myOverlay = self
        self.Downloader = Downloader()

        if not USING_FRODO:
            self.setCoordinateResolution(1)

        self.timeStarted = 0
        self.infoOnChange = True
        self.infoOffset = 0
        self.invalidatedChannelCount = 0
        self.showingInfo = False
        self.showChannelBug = False
        self.notificationLastChannel = 0
        self.notificationLastShow = 0
        self.notificationShowedNotif = False
        self.isExiting = False
        self.maxChannels = 0
        self.notPlayingCount = 0
        self.ignoreInfoAction = False
        self.shortItemLength = 60
        self.runningActionChannel = 0
        self.channelDelay = 0
        self.channelbugcolor = ''
        # self.channelbugcolor = '0xC0C0C0C0'  #Cyan
        # self.channelbugcolor = '0xFF0297eb'  #Holo
        
        global InfoTimer 
        InfoTimer = INFOBAR_TIMER[int(REAL_SETTINGS.getSetting('InfoTimer'))]
        self.log("InfoTimer = " + str(InfoTimer))

        for i in range(3):
            self.channelLabel.append(xbmcgui.ControlImage(50 + (50 * i), 50, 50, 50, DEFAULT_IMAGES_LOC + 'solid.png', colorDiffuse = self.channelbugcolor))
            self.addControl(self.channelLabel[i])
            self.channelLabel[i].setVisible(False)
        self.doModal()
        self.log('__init__ return')


    def resetChannelTimes(self):
        for i in range(self.maxChannels):
            self.channels[i].setAccessTime(self.timeStarted - self.channels[i].totalTimePlayed)

            
    def onFocus(self, controlId):
        pass

        
    # override the doModal function so we can setup everything first
    def onInit(self):
        self.log('onInit')
        self.log('PTVL Version = ' + VERSION)
        self.channelList = ChannelList()
        
        settingsFile = xbmc.translatePath(os.path.join(SETTINGS_LOC, 'settings2.xml'))
        nsettingsFile = xbmc.translatePath(os.path.join(SETTINGS_LOC, 'settings2.bak.xml'))
        dlg = xbmcgui.Dialog()
        thumb = (DEFAULT_IMAGES_LOC + 'icon.png')
        
        # Clear Setting2 for fresh autotune
        if REAL_SETTINGS.getSetting("Autotune") == "true" and REAL_SETTINGS.getSetting("Warning1") == "true":
            self.log('Autotune onInit') 
            if os.path.exists(settingsFile):
                if os.path.exists(nsettingsFile):
                    os.remove(nsettingsFile)
                    xbmc.log('Autotune, Removing old Backup...')

                FileAccess.rename(settingsFile, nsettingsFile)
                xbmc.log('Autotune, Backing up Setting2...')
                                
                if os.path.exists(nsettingsFile):
                    xbmc.log('Autotune, Back Complete!')
                    f = FileAccess.open(settingsFile, "w")
                    f.write('\n')
                    self.log('Autotune, Setting2 Deleted...')
                    f.close()
                    xbmc.executebuiltin("Notification( %s, %s, %d, %s)" % ("PseudoTV Live", "Initializing Autotuning...", 4000, thumb) )
                    xbmc.log('Autotune, Setting2 Deleted...')

        if FileAccess.exists(GEN_CHAN_LOC) == False:
            try:
                FileAccess.makedirs(GEN_CHAN_LOC)
            except:
                self.Error('Unable to create the cache directory')
                return

        if FileAccess.exists(MADE_CHAN_LOC) == False:
            try:
                FileAccess.makedirs(MADE_CHAN_LOC)
            except:
                self.Error('Unable to create the storage directory')
                return

        self.background = self.getControl(101)
        self.getControl(102).setVisible(False)
        self.background.setVisible(True)
        updateDialog = xbmcgui.DialogProgress()
        updateDialog.create("PseudoTV Live", "Initializing")
        self.backupFiles(updateDialog)
        ADDON_SETTINGS.loadSettings()
        
        if CHANNEL_SHARING:
            FileAccess.makedirs(LOCK_LOC)
            updateDialog.update(70, "Initializing", "Checking Other Instances")
            updateDialog.update(70, "Initializing", "Checking Other Instances")
            self.isMaster = GlobalFileLock.lockFile("MasterLock", False)
        else:
            self.isMaster = True

        updateDialog.update(95, "Initializing", "Migrating")

        if self.isMaster:
            migratemaster = Migrate()
            migratemaster.migrate()

        self.channelLabelTimer = threading.Timer(5.0, self.hideChannelLabel)
        self.playerTimer = threading.Timer(2.0, self.playerTimerAction)
        self.playerTimer.name = "PlayerTimer"
        self.infoTimer = threading.Timer(5.0, self.hideInfo)
        self.myEPG = EPGWindow("script.pseudotv.live.EPG.xml", ADDON_INFO, Skin_Select)
        self.myEPG.MyOverlayWindow = self
        
        # Don't allow any actions during initialization
        self.actionSemaphore.acquire()
        updateDialog.close()
        self.timeStarted = time.time()

        if self.readConfig() == False:
            return

        self.myEPG.channelLogos = self.channelLogos
        self.maxChannels = len(self.channels)

        if self.maxChannels == 0:
            autoTune = False
            dlg = xbmcgui.Dialog()
            REAL_SETTINGS.setSetting("Autotune","true")
            REAL_SETTINGS.setSetting("Warning1","true")
            
            if dlg.yesno("No Channels Configured", "Would you like PseudoTV Live to Auto Tune LiveTV PVR Backend\nchannels the next time it loads?"):
                REAL_SETTINGS.setSetting("autoFindLivePVR","true")
                autoTune = True
                
            if dlg.yesno("No Channels Configured", "Would you like PseudoTV Live to Auto Tune Custom Playlists\nchannels the next time it loads?"):
                REAL_SETTINGS.setSetting("autoFindCustom","true")
                autoTune = True
                
            if dlg.yesno("No Channels Configured", "Would you like PseudoTV Live to Auto Tune TV Network\nchannels the next time it loads?"):
                REAL_SETTINGS.setSetting("autoFindNetworks","true")
                autoTune = True

            if dlg.yesno("No Channels Configured", "Would you like PseudoTV Live to Auto Tune TV Genre\nchannels the next time it loads?"):
                REAL_SETTINGS.setSetting("autoFindTVGenre","true")
                autoTune = True

            if dlg.yesno("No Channels Configured", "Would you like PseudoTV Live to Auto Tune Movie Studio\nchannels the next time it loads?"):
                REAL_SETTINGS.setSetting("autoFindStudios","true")
                autoTune = True

            if dlg.yesno("No Channels Configured", "Would you like PseudoTV Live to Auto Tune Movie Genre\nchannels the next time it loads?"):
                REAL_SETTINGS.setSetting("autoFindMovieGenres","true")
                autoTune = True

            if dlg.yesno("No Channels Configured", "Would you like PseudoTV Live to Auto Tune Mix Genre\nchannels the next time it loads?"):
                REAL_SETTINGS.setSetting("autoFindMixGenres","true")
                autoTune = True

            if dlg.yesno("No Channels Configured", "Would you like PseudoTV Live to Auto Tune InternetTV\nchannels the next time it loads?"):
                REAL_SETTINGS.setSetting("autoFindInternetStrms","true")
                autoTune = True

            if autoTune:
                self.end()
                return
            else:
                REAL_SETTINGS.setSetting("Autotune","false")
                REAL_SETTINGS.setSetting("Warning1","false")
                self.Error('Unable to find any channels. \nPlease go to the Addon Settings to configure PseudoTV Live.')
                self.end()
                return   
            del dlg

        if self.maxChannels == 0:
            self.Error('Unable to find any channels. Please configure the addon.')
            return

        found = False

        for i in range(self.maxChannels):
            if self.channels[i].isValid:
                found = True
                break

        if found == False:
            self.Error("Unable to populate channels. Please verify that you", "have scraped media in your library and that you have", "properly configured channels.")
            return

        if self.sleepTimeValue > 0:
            self.sleepTimer = threading.Timer(self.sleepTimeValue, self.sleepAction)

        self.notificationTimer = threading.Timer(NOTIFICATION_CHECK_TIME, self.notificationAction)

        try:
            if self.forceReset == False:
                self.currentChannel = self.fixChannel(int(REAL_SETTINGS.getSetting("CurrentChannel")))
            else:
                self.currentChannel = self.fixChannel(1)
        except:
            self.currentChannel = self.fixChannel(1)

        self.resetChannelTimes()
        self.setChannel(self.currentChannel)
        self.background.setVisible(False)
        self.startSleepTimer()
        self.startNotificationTimer()
        self.playerTimer.start()

        if self.backgroundUpdating < 2 or self.isMaster == False:
            self.channelThread.name = "ChannelThread"
            self.channelThread.start()

        self.actionSemaphore.release()
        self.log('onInit return')

    # setup all basic configuration parameters, including creating the playlists that
    # will be used to actually run this thing
    def readConfig(self):
        self.log('readConfig')
        # Sleep setting is in 30 minute increments...so multiply by 30, and then 60 (min to sec)
        self.sleepTimeValue = int(REAL_SETTINGS.getSetting('AutoOff')) * 1800
        self.log('Auto off is ' + str(self.sleepTimeValue))
        self.infoOnChange = REAL_SETTINGS.getSetting("InfoOnChange") == "true"
        self.log('Show info label on channel change is ' + str(self.infoOnChange))
        self.showChannelBug = REAL_SETTINGS.getSetting("ShowChannelBug") == "true"
        self.log('Show channel bug - ' + str(self.showChannelBug))
        self.forceReset = REAL_SETTINGS.getSetting('ForceChannelReset') == "true"
        self.channelResetSetting = REAL_SETTINGS.getSetting('ChannelResetSetting')
        self.log("Channel reset setting - " + str(self.channelResetSetting))
        self.channelLogos = xbmc.translatePath(REAL_SETTINGS.getSetting('ChannelLogoFolder'))
        self.backgroundUpdating = int(REAL_SETTINGS.getSetting("ThreadMode"))
        self.log("Background updating - " + str(self.backgroundUpdating))
        self.showNextItem = REAL_SETTINGS.getSetting("EnableComingUp") == "true"
        self.log("Show Next Item - " + str(self.showNextItem))
        self.hideShortItems = REAL_SETTINGS.getSetting("HideClips") == "true"
        self.log("Hide Short Items - " + str(self.hideShortItems))
        self.shortItemLength = SHORT_CLIP_ENUM[int(REAL_SETTINGS.getSetting("ClipLength"))]
        self.log("Short item length - " + str(self.shortItemLength))
        self.channelDelay = int(REAL_SETTINGS.getSetting("ChannelDelay")) * 250

        if FileAccess.exists(self.channelLogos) == False:
            self.channelLogos = IMAGES_LOC

        self.log('Channel logo folder - ' + self.channelLogos)
        self.channelList = ChannelList()
        self.channelList.myOverlay = self
        self.channels = self.channelList.setupList()

        if self.channels is None:
            self.log('readConfig No channel list returned')
            self.end()
            return False

        self.Player.stop()
        self.log('readConfig return')
        return True

        
    # handle fatal errors: log it, show the dialog, and exit
    def Error(self, line1, line2 = '', line3 = ''):
        self.log('FATAL ERROR: ' + line1 + " " + line2 + " " + line3, xbmc.LOGFATAL)
        dlg = xbmcgui.Dialog()
        dlg.ok('Error', line1, line2, line3)
        del dlg
        self.end()

        
    def channelDown(self):
        self.log('channelDown')

        if self.maxChannels == 1:
            return

        self.background.setVisible(True)
        channel = self.fixChannel(self.currentChannel - 1, False)
        self.setChannel(channel)
        self.background.setVisible(False)
        self.log('channelDown return')
        
        
    def backupFiles(self, updatedlg):
        self.log('backupFiles')

        if CHANNEL_SHARING == False:
            return

        updatedlg.update(1, "Initializing", "Copying Channels...")
        realloc = REAL_SETTINGS.getSetting('SettingsFolder')
        FileAccess.copy(realloc + '/settings2.xml', SETTINGS_LOC + '/settings2.xml')
        realloc = xbmc.translatePath(os.path.join(realloc, 'cache')) + '/'

        for i in range(999):
            FileAccess.copy(realloc + 'channel_' + str(i) + '.m3u', CHANNELS_LOC + 'channel_' + str(i) + '.m3u')
            updatedlg.update(int(i * .07) + 1, "Initializing", "Copying Channels...")


    def storeFiles(self):
        self.log('storeFiles')

        if CHANNEL_SHARING == False:
            return

        realloc = REAL_SETTINGS.getSetting('SettingsFolder')
        FileAccess.copy(SETTINGS_LOC + '/settings2.xml', realloc + '/settings2.xml')
        realloc = xbmc.translatePath(os.path.join(realloc, 'cache')) + '/'

        for i in range(self.maxChannels):
            if self.channels[i].isValid:
                FileAccess.copy(CHANNELS_LOC + 'channel_' + str(i) + '.m3u', realloc + 'channel_' + str(i) + '.m3u')


    def channelUp(self):
        self.log('channelUp')

        if self.maxChannels == 1:
            return

        self.background.setVisible(True)
        channel = self.fixChannel(self.currentChannel + 1)
        self.setChannel(channel)
        self.background.setVisible(False)
        self.log('channelUp return')

        
    def message(self, data):
        self.log('Dialog message: ' + data)
        dlg = xbmcgui.Dialog()
        dlg.ok('Info', data)
        del dlg


    def log(self, msg, level = xbmc.LOGDEBUG):
        log('TVOverlay: ' + msg, level)

        
    def logDebug(self, msg, level = xbmc.LOGDEBUG):
        if REAL_SETTINGS.getSetting('enable_Debug') == "true":
            log('TVOverlay: ' + msg, level) 
            
    
    # set the channel, the proper show offset, and time offset
    def setChannel(self, channel):
        self.log('setChannel ' + str(channel))
        self.runActions(RULES_ACTION_OVERLAY_SET_CHANNEL, channel, self.channels[channel - 1])

        if self.Player.stopped:
            self.log('setChannel player already stopped', xbmc.LOGERROR);
            return

        if channel < 1 or channel > self.maxChannels:
            self.log('setChannel invalid channel ' + str(channel), xbmc.LOGERROR)
            return

        if self.channels[channel - 1].isValid == False:
            self.log('setChannel channel not valid ' + str(channel), xbmc.LOGERROR)
            return

        self.lastActionTime = 0
        timedif = 0
        self.getControl(102).setVisible(False)
        self.getControl(103).setImage('')
        self.showingInfo = False

        # first of all, save playing state, time, and playlist offset for
        # the currently playing channel
        if self.Player.isPlaying():
            if channel != self.currentChannel:
                self.channels[self.currentChannel - 1].setPaused(xbmc.getCondVisibility('Player.Paused'))

                # Automatically pause in serial mode
                if self.channels[self.currentChannel - 1].mode & MODE_ALWAYSPAUSE > 0:
                    self.channels[self.currentChannel - 1].setPaused(True)

                self.channels[self.currentChannel - 1].setShowTime(self.Player.getTime())
                self.channels[self.currentChannel - 1].setShowPosition(xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition())
                self.channels[self.currentChannel - 1].setAccessTime(time.time())

        self.currentChannel = channel
        # now load the proper channel playlist
        xbmc.PlayList(xbmc.PLAYLIST_MUSIC).clear()
        self.log("about to load");

        if xbmc.PlayList(xbmc.PLAYLIST_MUSIC).load(self.channels[channel - 1].fileName) == False:
            self.log("Error loading playlist", xbmc.LOGERROR)
            self.InvalidateChannel(channel)
            return

        # Disable auto playlist shuffling if it's on
        if xbmc.getInfoLabel('Playlist.Random').lower() == 'random':
            self.log('Random on.  Disabling.')
            xbmc.PlayList(xbmc.PLAYLIST_MUSIC).unshuffle()

        chtype = int(ADDON_SETTINGS.getSetting('Channel_' + str(self.currentChannel) + '_type'))
        self.log("repeat all");
        xbmc.executebuiltin("PlayerControl(repeatall)")
        curtime = time.time()
        timedif = (curtime - self.channels[self.currentChannel - 1].lastAccessTime)

        if self.channels[self.currentChannel - 1].isPaused == False:
         
            # adjust the show and time offsets to properly position inside the playlist
            #for Live TV get the first item in playlist convert to epoch time  add duration until we get to the current item
            if chtype == 8:
                 self.channels[self.currentChannel - 1].setShowPosition(0)
                 tmpDate = self.channels[self.currentChannel - 1].getItemtimestamp(0)
                 self.log("overlay tmpdate " + str(tmpDate))
                 t = time.strptime(tmpDate, '%Y-%m-%d %H:%M:%S')
                 epochBeginDate = time.mktime(t)
                 #beginDate = datetime.datetime(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
                 #index til we get to the current show
                 while epochBeginDate + self.channels[self.currentChannel - 1].getCurrentDuration() <  curtime:
                    self.log('epoch '+ str(epochBeginDate) + ', ' + 'time ' + str(curtime))
                    epochBeginDate += self.channels[self.currentChannel - 1].getCurrentDuration()
                    self.channels[self.currentChannel - 1].addShowPosition(1)
                    self.log('live tv overlay while loop')
            else:   #loop  for other channel types
                 while self.channels[self.currentChannel - 1].showTimeOffset + timedif > self.channels[self.currentChannel - 1].getCurrentDuration():
                    timedif -= self.channels[self.currentChannel - 1].getCurrentDuration() - self.channels[self.currentChannel - 1].showTimeOffset
                    self.channels[self.currentChannel - 1].addShowPosition(1)
                    self.channels[self.currentChannel - 1].setShowTime(0)
                    self.log('while loop')

        # First, check to see if the video is a...
        if self.channels[self.currentChannel - 1].getItemFilename(self.channels[self.currentChannel - 1].playlistPosition)[-4:].lower() == 'strm' or self.channels[self.currentChannel - 1].getItemFilename(self.channels[self.currentChannel - 1].playlistPosition)[-9:].lower() == 'hdhomerun' or self.channels[self.currentChannel - 1].getItemFilename(self.channels[self.currentChannel - 1].playlistPosition)[-6:].lower() == 'plugin' or self.channels[self.currentChannel - 1].getItemFilename(self.channels[self.currentChannel - 1].playlistPosition)[-4:].lower() == 'rtmp' or self.channels[self.currentChannel - 1].getItemFilename(self.channels[self.currentChannel - 1].playlistPosition)[-4:].lower() == 'http' or self.channels[self.currentChannel - 1].getItemFilename(self.channels[self.currentChannel - 1].playlistPosition)[-3:].lower() == 'pvr':
            self.log("Ignoring a stop because of a strm")
            self.Player.ignoreNextStop = True

        self.log("about to mute");
        # Mute the channel before changing
        xbmc.executebuiltin("Mute()");
        xbmc.sleep(self.channelDelay)
        # set the show offset
        self.Player.playselected(self.channels[self.currentChannel - 1].playlistPosition)
        self.log("playing selected file");
        # set the time offset
        self.channels[self.currentChannel - 1].setAccessTime(curtime)

        if self.channels[self.currentChannel - 1].isPaused:
            self.channels[self.currentChannel - 1].setPaused(False)

            try:
                self.Player.seekTime(self.channels[self.currentChannel - 1].showTimeOffset)

                if self.channels[self.currentChannel - 1].mode & MODE_ALWAYSPAUSE == 0:
                    self.Player.pause()

                    if self.waitForVideoPaused() == False:
                        xbmc.executebuiltin("Mute()");
                        return
            except:
                self.log('Exception during seek on paused channel', xbmc.LOGERROR)
        else:
            seektime = self.channels[self.currentChannel - 1].showTimeOffset + timedif + int((time.time() - curtime))

            try:
                self.log("Seeking");
                self.Player.seekTime(seektime)
            except:
                self.log("Unable to set proper seek time, trying different value")

                try:
                    seektime = self.channels[self.currentChannel - 1].showTimeOffset + timedif
                    self.Player.seekTime(seektime)
                except:
                    self.log('Exception during seek', xbmc.LOGERROR)

        # Unmute
        self.log("Finished, unmuting");
        xbmc.executebuiltin("Mute()");
        self.showChannelLabel(self.currentChannel)
        self.lastActionTime = time.time()
        self.runActions(RULES_ACTION_OVERLAY_SET_CHANNEL_END, channel, self.channels[channel - 1])
        self.log('setChannel return')

        
    def InvalidateChannel(self, channel):
        self.log("InvalidateChannel" + str(channel))

        if channel < 1 or channel > self.maxChannels:
            self.log("InvalidateChannel invalid channel " + str(channel))
            return

        self.channels[channel - 1].isValid = False
        self.invalidatedChannelCount += 1

        if self.invalidatedChannelCount > 3:
            self.Error("Exceeded 3 invalidated channels. Exiting.")
            return

        remaining = 0

        for i in range(self.maxChannels):
            if self.channels[i].isValid:
                remaining += 1

        if remaining == 0:
            self.Error("No channels available. Exiting.")
            return

        self.setChannel(self.fixChannel(channel))
    
    
    def waitForVideoPaused(self):
        self.log('waitForVideoPaused')
        sleeptime = 0

        while sleeptime < TIMEOUT:
            xbmc.sleep(100)

            if self.Player.isPlaying():
                if xbmc.getCondVisibility('Player.Paused'):
                    break

            sleeptime += 100
        else:
            self.log('Timeout waiting for pause', xbmc.LOGERROR)
            return False

        self.log('waitForVideoPaused return')
        return True

    
    def setShowInfo(self):
        self.log('setShowInfo')
        chtype = int(ADDON_SETTINGS.getSetting('Channel_' + str(self.currentChannel) + '_type'))
        # if chtype == 8:
            # self.showingInfo = False
        # else:
            # self.showingInfo = True
            
        if self.infoOffset > 0:
            self.getControl(502).setLabel('COMING UP:')
        elif self.infoOffset < 0:
            self.getControl(502).setLabel('ALREADY SEEN:')
        elif self.infoOffset == 0:
            self.getControl(502).setLabel('NOW WATCHING:')

        if self.hideShortItems and self.infoOffset != 0:
            position = xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition()
            curoffset = 0
            modifier = 1

            if self.infoOffset < 0:
                modifier = -1

            while curoffset != abs(self.infoOffset):
                position = self.channels[self.currentChannel - 1].fixPlaylistIndex(position + modifier)

                if self.channels[self.currentChannel - 1].getItemDuration(position) >= self.shortItemLength:
                    curoffset += 1
        else:
            #same logic as in setchannel; loop till we get the current show
            if chtype == 8:
                 self.channels[self.currentChannel - 1].setShowPosition(0)
                 tmpDate = self.channels[self.currentChannel - 1].getItemtimestamp(0)
                 t = time.strptime(tmpDate, '%Y-%m-%d %H:%M:%S')
                 epochBeginDate = time.mktime(t)
                 position = self.channels[self.currentChannel - 1].playlistPosition
                 #beginDate = datetime.datetime(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
                 #loop till we get to the current show this is done to display the correct show on the info listing for Live TV types
                 while epochBeginDate + self.channels[self.currentChannel - 1].getCurrentDuration() <  time.time():
                    epochBeginDate += self.channels[self.currentChannel - 1].getCurrentDuration()
                    self.channels[self.currentChannel - 1].addShowPosition(1)
                    position = self.channels[self.currentChannel - 1].playlistPosition
            else: #original code
                position = xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition() + self.infoOffset
        
        if REAL_SETTINGS.getSetting("fandb.enabled") == "true":
            self.apis = True
        else:
            self.apis = False
            
        type = ''
        tvdbid = 0
        imdbid = 0
        dbid = 0
        
        mediapath = uni(self.channels[self.currentChannel - 1].getItemFilename(position))
        self.logDebug('setShowInfo.mediapath.1 = ' + uni(mediapath))
        
        genre = uni(self.channels[self.currentChannel - 1].getItemgenre(position))
        title = uni(self.channels[self.currentChannel - 1].getItemTitle(position))
        
        LiveID = uni(self.channels[self.currentChannel - 1].getItemLiveID(position))
        self.logDebug('setShowInfo.LiveID = ' + uni(LiveID))
        
        try:
            type1 = str(self.getControl(507).getLabel())
            self.logDebug('setShowInfo.type1 = ' + str(type1))  
        except:
            pass
        
        try:
            type2 = str(self.getControl(509).getLabel())
            self.logDebug('setShowInfo.type2 = ' + str(type2))  
        except:
            pass
        
        jpg = ['banner', 'fanart', 'folder', 'landscape', 'poster']
        png = ['character', 'clearart', 'logo']
        
        if type1 in jpg:
            type1EXT = (type1 + '.jpg')
        else:
            type1EXT = (type1 + '.png')
        self.logDebug('setShowInfo.type1.ext = ' + str(type1EXT))  
        
        if type2 in jpg:
            type2EXT = (type2 + '.jpg')
        else:
            type2EXT = (type2 + '.png')
        self.logDebug('setShowInfo.type2.ext = ' + str(type2EXT))   
        
        #rename art types for script.artwork.downloader
        arttype1 = type1.replace("folder", "poster").replace("landscape", "thumb").replace("character", "characterart").replace("logo", "clearlogo")
        arttype2 = type2.replace("folder", "poster").replace("landscape", "thumb").replace("character", "characterart").replace("logo", "clearlogo")
        
        if not 'LiveID' in LiveID:
            try:
                LiveLST = LiveID.split("|", 4)
                self.logDebug('setShowInfo.LiveLST = ' + str(LiveLST))
                imdbid = LiveLST[0]
                self.logDebug('setShowInfo.LiveLST.imdbid.1 = ' + str(imdbid))
                imdbid = imdbid.split('imdb_', 1)[-1]
                self.logDebug('setShowInfo.LiveLST.imdbid.2 = ' + str(imdbid))
                tvdbid = LiveLST[1]
                self.logDebug('setShowInfo.LiveLST.tvdbid.1 = ' + str(tvdbid))
                tvdbid = tvdbid.split('tvdb_', 1)[-1]
                self.logDebug('setShowInfo.LiveLST.tvdbid.2 = ' + str(tvdbid))
                SBCP = LiveLST[2]
                self.logDebug('setShowInfo.LiveLST.SBCP = ' + str(SBCP))
                
                if 'dbid_' in LiveLST[3]:
                    dbidTYPE = LiveLST[3]
                    self.logDebug('setShowInfo.LiveLST.dbidTYPE.1 = ' + str(dbidTYPE))
                    dbidTYPE = dbidTYPE.split('dbid_', 1)[-1]
                    self.logDebug('setShowInfo.LiveLST.dbidTYPE.2 = ' + str(dbidTYPE))
                    dbid = dbidTYPE.split(',')[0]
                    self.logDebug('setShowInfo.LiveLST.dbid = ' + str(dbid))
                    type = dbidTYPE.split(',', 1)[-1]
                    self.logDebug('setShowInfo.LiveLST.type = ' + str(type))
                    
                    if arttype1 == 'thumb' and type == 'tvshow':
                        arttype1 = ('tv' + arttype1)
                    if arttype2 == 'thumb' and type == 'tvshow':
                        arttype2 = ('tv' + arttype2)
                    
                    if type == 'tvshow':
                        id = tvdbid
                    elif type == 'movie':
                        id = imdbid
                    
                else:
                    UnairedTYPE = LiveLST[3]
                    self.logDebug('setShowInfo.LiveLST.UnairedTYPE = ' + str(UnairedTYPE))
                    UnairedTYPE = UnairedTYPE.split('dbid_', 1)[-1]
                    self.logDebug('setShowInfo.LiveLST.UnairedTYPE.2 = ' + str(UnairedTYPE))
                    Unaired = UnairedTYPE.split(',')[0]
                    self.logDebug('setShowInfo.LiveLST.Unaired = ' + str(Unaired))
                    type = UnairedTYPE.split(',', 1)[-1]
                    self.logDebug('setShowInfo.LiveLST.type = ' + str(type))
            except:
                self.log('setShowInfo.LiveLST Failed')
                pass     
            
            try:
                #Try, and pass if label isn't found (Backward compatibility with PTV Skins)
                #Sickbeard/Couchpotato
                if SBCP == 'SB':
                    self.getControl(511).setImage(DEFAULT_IMAGES_LOC + 'SB.png')
                elif SBCP == 'CP':
                    self.getControl(511).setImage(DEFAULT_IMAGES_LOC + 'CP.png')
                else:
                    self.getControl(511).setImage(DEFAULT_IMAGES_LOC + 'NA.png')
            except:
                self.getControl(511).setImage(DEFAULT_IMAGES_LOC + 'NA.png')
                pass     

            try:
                #Try, and pass if label isn't found (Backward compatibility with PTV Skins)             
                #Unaired/aired
                if Unaired == 'NEW':
                    self.getControl(512).setImage(MEDIA_LOC + 'NEW.png')
                elif Unaired == 'OLD':
                    self.getControl(512).setImage(MEDIA_LOC + 'OLD.png')                  
                else:
                    self.getControl(512).setImage(MEDIA_LOC + 'NA.png')
            except:
                self.getControl(512).setImage(MEDIA_LOC + 'NA.png')
                pass     

        if REAL_SETTINGS.getSetting("art.enable") == "true":
            self.log('setShowInfo.Dynamic artwork enabled')
        
            if chtype <= 7:
                mediapathSeason, filename = os.path.split(mediapath)
                mediapathSeries = os.path.dirname(mediapathSeason)
                
                mediapathSeries1 = ascii(os.path.join(mediapathSeries, type1EXT))
                mediapathSeason1 = ascii(os.path.join(mediapathSeason, type1EXT))
                
                self.logDebug('setShowInfo.mediapathSeries = ' + uni(mediapathSeries1))
                self.logDebug('setShowInfo.mediapathSeason = ' + uni(mediapathSeason1))  

                if xbmcvfs.exists(mediapathSeries1):
                    self.getControl(508).setImage(mediapathSeries1)
                elif xbmcvfs.exists(mediapathSeason1):
                    self.getControl(508).setImage(mediapathSeason1)
                else:
                    self.getControl(508).setImage(MEDIA_LOC + type1 + '.png')
                    
                    # if REAL_SETTINGS.getSetting("EnableDown") == "1" and (REAL_SETTINGS.getSetting("TVFileSys") == "0" or REAL_SETTINGS.getSetting("MovieFileSys") == "0") and self.apis == True:
                        # self.Downloader.ArtDownloader(type, id, type2, type2EXT, Mpath1, Ipath1)
                    
                    # elif REAL_SETTINGS.getSetting("EnableDown") == "1" and (REAL_SETTINGS.getSetting("TVFileSys") == "1" or REAL_SETTINGS.getSetting("MovieFileSys") == "1") and self.apis == True:
                        # self.Downloader.ArtDownloader(type, id, type2, type2EXT, Mpath1, Ipath1)
                    
                    if REAL_SETTINGS.getSetting("EnableDown") == "2" and REAL_SETTINGS.getSetting("EnableDownSilent") == "false" and chtype != 7:
                        xbmc.executebuiltin('XBMC.runscript(script.artwork.downloader, mode=gui, mediatype='+type+', dbid='+dbid+', '+arttype1+')')
                    
                    elif REAL_SETTINGS.getSetting("EnableDown") == "2" and REAL_SETTINGS.getSetting("EnableDownSilent") == "true" and chtype != 7:
                        xbmc.executebuiltin('XBMC.runscript(script.artwork.downloader, silent=true, mediatype='+type+', dbid='+dbid+', '+arttype1+')')
            
                mediapathSeries2 = ascii(os.path.join(mediapathSeries, type2EXT))
                mediapathSeason2 = ascii(os.path.join(mediapathSeason, type2EXT))
                
                if xbmcvfs.exists(mediapathSeries2):
                    self.getControl(510).setImage(mediapathSeries2)
                elif xbmcvfs.exists(mediapathSeason2):
                    self.getControl(510).setImage(mediapathSeason2)
                else:
                    self.getControl(510).setImage(MEDIA_LOC + type2 + '.png')
                        
                    # if REAL_SETTINGS.getSetting("EnableDown") == "1" and (REAL_SETTINGS.getSetting("TVFileSys") == "0" or REAL_SETTINGS.getSetting("MovieFileSys") == "0") and self.apis == True:
                        # self.Downloader.ArtDownloader(type, id, type2, type2EXT, Mpath2, Ipath2)
                    
                    # elif REAL_SETTINGS.getSetting("EnableDown") == "1" and (REAL_SETTINGS.getSetting("TVFileSys") == "1" or REAL_SETTINGS.getSetting("MovieFileSys") == "1") and self.apis == True:
                        # self.Downloader.ArtDownloader(type, id, type2, type2EXT, Mpath2, Ipath2)
                    
                    if REAL_SETTINGS.getSetting("EnableDown") == "2" and REAL_SETTINGS.getSetting("EnableDownSilent") == "false" and chtype != 7:
                        xbmc.executebuiltin('XBMC.runscript(script.artwork.downloader, mode=gui, mediatype='+type+', dbid='+dbid+', '+arttype2+')')
                    
                    elif REAL_SETTINGS.getSetting("EnableDown") == "2" and REAL_SETTINGS.getSetting("EnableDownSilent") == "true" and chtype != 7:
                        xbmc.executebuiltin('XBMC.runscript(script.artwork.downloader, silent=true, mediatype='+type+', dbid='+dbid+', '+arttype2+')')
                
            #LiveTV w/ TVDBID via Fanart.TV        
            elif chtype == 8:
                if REAL_SETTINGS.getSetting('Live.art.enable') == 'true' and self.apis == True:
                    self.log('LiveTV Art Enabled')
                    self.LiveTVArtDownloader(imdbid, tvdbid, type, type1, type1EXT, type2, type2EXT)       
                else:#fallback all artwork because live art disabled
                    self.getControl(508).setImage(MEDIA_LOC + type1 + '.png')
                    self.getControl(510).setImage(MEDIA_LOC + type2 + '.png')

            elif chtype == 9:
                self.getControl(508).setImage(MEDIA_LOC + 'Overlay.Internet.508.png')
                self.getControl(510).setImage(MEDIA_LOC + 'Overlay.Internet.510.png')
            
            elif chtype == 10:
                self.getControl(508).setImage(MEDIA_LOC + 'Overlay.Youtube.508.png')
                self.getControl(510).setImage(MEDIA_LOC + 'Overlay.Youtube.510.png')
            
            elif chtype == 11:
                self.getControl(508).setImage(MEDIA_LOC + 'Overlay.RSS.508.png')
                self.getControl(510).setImage(MEDIA_LOC + 'Overlay.RSS.510.png')    
            
            elif chtype == 13:
                self.getControl(508).setImage(MEDIA_LOC + 'Overlay.LastFM.508.png')
                self.getControl(510).setImage(MEDIA_LOC + 'Overlay.LastFM.510.png')    


        self.log('setshowposition ' + str(position))
        self.getControl(503).setLabel(self.channels[self.currentChannel - 1].getItemTitle(position))
        self.getControl(504).setLabel(self.channels[self.currentChannel - 1].getItemEpisodeTitle(position))
        self.getControl(505).setLabel(self.channels[self.currentChannel - 1].getItemDescription(position))
       
        if REAL_SETTINGS.getSetting("ColorOverlay") == "true":
            self.getControl(506).setImage(self.channelLogos + ascii(self.channels[self.currentChannel - 1].name) + '_c.png')
        else:
            self.getControl(506).setImage(self.channelLogos + ascii(self.channels[self.currentChannel - 1].name) + '.png')
        self.log('setShowInfo return')


    # Display the current channel based on self.currentChannel.
    # Start the timer to hide it.
    def showChannelLabel(self, channel):
        self.log('showChannelLabel ' + str(channel))

        if self.channelLabelTimer.isAlive():
            self.channelLabelTimer.cancel()
            self.channelLabelTimer = threading.Timer(5.0, self.hideChannelLabel)

        tmp = self.inputChannel
        self.hideChannelLabel()
        self.inputChannel = tmp
        curlabel = 0

        if channel > 99:
            self.channelLabel[curlabel].setImage(IMAGES_LOC + 'label_' + str(channel // 100) + '.png')
            self.channelLabel[curlabel].setVisible(True)
            curlabel += 1

        if channel > 9:
            self.channelLabel[curlabel].setImage(IMAGES_LOC + 'label_' + str((channel % 100) // 10) + '.png')
            self.channelLabel[curlabel].setVisible(True)
            curlabel += 1

        self.channelLabel[curlabel].setImage(IMAGES_LOC + 'label_' + str(channel % 10) + '.png')
        self.channelLabel[curlabel].setVisible(True)

        ##ADDED BY SRANSHAFT: USED TO SHOW NEW INFO WINDOW WHEN CHANGING CHANNELS
        if self.inputChannel == -1 and self.infoOnChange == True:
            self.log("InfoTimer = " + str(InfoTimer))
            self.infoOffset = 0
            self.showInfo(InfoTimer)

        if self.showChannelBug == True:      
            chtype = (ADDON_SETTINGS.getSetting('Channel_' + str(self.currentChannel - 1) + '_type'))
            
            if chtype == '8':
                #disable channel bug for LiveTV
                self.getControl(103).setImage('')
            else:
                if REAL_SETTINGS.getSetting("ColorOverlay") == "true":
                    self.getControl(103).setImage(self.channelLogos + ascii(self.channels[self.currentChannel - 1].name) + '_c.png')
                else:
                    self.getControl(103).setImage(self.channelLogos + ascii(self.channels[self.currentChannel - 1].name) + '.png')
        else:
            try:
                self.getControl(103).setImage('')
            except:
                pass
        try:
            self.getControl(300).setLabel(self.channels[self.currentChannel - 1].name)##Channel name label
        except:
            pass

        if xbmc.getCondVisibility('Player.ShowInfo'):
            if USING_FRODO:
                json_query = '{"jsonrpc": "2.0", "method": "Input.Info", "id": 1}'
                self.ignoreInfoAction = True
                self.channelList.sendJSON(json_query);
            else:
                xbmc.executehttpapi("SendKey(0xF049)")
                self.ignoreInfoAction = True

        self.channelLabelTimer.name = "ChannelLabel"
        self.channelLabelTimer.start()
        self.startNotificationTimer(10.0)
        self.log('showChannelLabel return')


    # Called from the timer to hide the channel label.
    def hideChannelLabel(self):
        self.log('hideChannelLabel')
        self.channelLabelTimer = threading.Timer(5.0, self.hideChannelLabel)

        for i in range(3):
            self.channelLabel[i].setVisible(False)

        self.inputChannel = -1
        self.log('hideChannelLabel return')


    def hideInfo(self):
        self.getControl(102).setVisible(False)
        self.infoOffset = 0
        self.showingInfo = False

        if self.infoTimer.isAlive():
            self.infoTimer.cancel()

        self.infoTimer = threading.Timer(5.0, self.hideInfo)


    def showInfo(self, timer):
        if self.hideShortItems:
            position = xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition() + self.infoOffset

            if self.channels[self.currentChannel - 1].getItemDuration(xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition()) < self.shortItemLength:
                return

        self.getControl(102).setVisible(True)
        self.showingInfo = True
        self.setShowInfo()

        if self.infoTimer.isAlive():
            self.infoTimer.cancel()

        self.infoTimer = threading.Timer(timer, self.hideInfo)
        self.infoTimer.name = "InfoTimer"

        if xbmc.getCondVisibility('Player.ShowInfo'):
            if USING_FRODO:
                json_query = '{"jsonrpc": "2.0", "method": "Input.Info", "id": 1}'
                self.ignoreInfoAction = True
                self.channelList.sendJSON(json_query);
            else:
                xbmc.executehttpapi("SendKey(0xF049)")
                self.ignoreInfoAction = True

        self.infoTimer.start()


    # return a valid channel in the proper range
    def fixChannel(self, channel, increasing = True):
        while channel < 1 or channel > self.maxChannels:
            if channel < 1: channel = self.maxChannels + channel
            if channel > self.maxChannels: channel -= self.maxChannels

        if increasing:
            direction = 1
        else:
            direction = -1

        if self.channels[channel - 1].isValid == False:
            return self.fixChannel(channel + direction, increasing)

        return channel


    # Handle all input while videos are playing
    def onAction(self, act):
        action = act.getId()
        self.log('onAction ' + str(action))
        chtype = int(ADDON_SETTINGS.getSetting('Channel_' + str(self.currentChannel) + '_type'))
        
        if self.Player.stopped:
            return

        # Since onAction isnt always called from the same thread (weird),
        # ignore all actions if we're in the middle of processing one
        if self.actionSemaphore.acquire(False) == False:
            self.log('Unable to get semaphore')
            return

        lastaction = time.time() - self.lastActionTime

        # during certain times we just want to discard all input
        if lastaction < 2:
            self.log('Not allowing actions')
            if chtype >= 7:
                action = ACTION_INVALID

        self.startSleepTimer()

        if action == ACTION_SELECT_ITEM:
            # If we're manually typing the channel, set it now
            if self.inputChannel > 0:
                if self.inputChannel != self.currentChannel:
                    self.setChannel(self.inputChannel)

                self.inputChannel = -1
            else:
                # Otherwise, show the EPG
                if self.channelThread.isAlive():
                    self.channelThread.pause()

                if self.notificationTimer.isAlive():
                    self.notificationTimer.cancel()
                    self.notificationTimer = threading.Timer(NOTIFICATION_CHECK_TIME, self.notificationAction)

                if self.sleepTimeValue > 0:
                    if self.sleepTimer.isAlive():
                        self.sleepTimer.cancel()
                        self.sleepTimer = threading.Timer(self.sleepTimeValue, self.sleepAction)

                self.hideInfo()
                self.newChannel = 0
                self.myEPG.doModal()

                if self.channelThread.isAlive():
                    self.channelThread.unpause()

                self.startNotificationTimer()

                if self.newChannel != 0:
                    self.background.setVisible(True)
                    self.setChannel(self.newChannel)
                    self.background.setVisible(False)
                    
        elif action == ACTION_MOVE_UP or action == ACTION_PAGEUP:
            self.channelUp()
        
        elif action == ACTION_MOVE_DOWN or action == ACTION_PAGEDOWN:
            self.channelDown()

        elif action == ACTION_MOVE_LEFT:
            if self.showingInfo:
                self.infoOffset -= 1
                self.showInfo(InfoTimer)
            else:
                if chtype != 8 or chtype != 9:
                    xbmc.executebuiltin("PlayerControl(SmallSkipBackward)")
        
        elif action == ACTION_MOVE_RIGHT:
            if self.showingInfo:
                self.infoOffset += 1
                self.showInfo(InfoTimer)
            else:
                if chtype != 8 or chtype != 9:          
                    xbmc.executebuiltin("PlayerControl(SmallSkipForward)")
        
        elif action in ACTION_PREVIOUS_MENU:
            if self.showingInfo:
                self.hideInfo()
            else:
                dlg = xbmcgui.Dialog()

                if self.sleepTimeValue > 0:
                    if self.sleepTimer.isAlive():
                        self.sleepTimer.cancel()
                        self.sleepTimer = threading.Timer(self.sleepTimeValue, self.sleepAction)

                if dlg.yesno("Exit?", "Are you sure you want to exit PseudoTV?"):
                    self.end()
                    return  # Don't release the semaphore
                else:
                    self.startSleepTimer()

                del dlg
        
        elif action == ACTION_SHOW_INFO:
            if self.ignoreInfoAction:
                self.ignoreInfoAction = False
            else:
                if self.showingInfo:
                    self.hideInfo()

                    if xbmc.getCondVisibility('Player.ShowInfo'):
                        if USING_FRODO:
                            json_query = '{"jsonrpc": "2.0", "method": "Input.Info", "id": 1}'
                            self.ignoreInfoAction = True
                            self.channelList.sendJSON(json_query);
                        else:
                            xbmc.executehttpapi("SendKey(0xF049)")
                            self.ignoreInfoAction = True
                else:
                    self.showInfo(InfoTimer)
        elif action >= ACTION_NUMBER_0 and action <= ACTION_NUMBER_9:
            if self.inputChannel < 0:
                self.inputChannel = action - ACTION_NUMBER_0
            else:
                if self.inputChannel < 100:
                    self.inputChannel = self.inputChannel * 10 + action - ACTION_NUMBER_0

            self.showChannelLabel(self.inputChannel)
        elif action == ACTION_OSD:
            xbmc.executebuiltin("ActivateWindow(12901)")

        self.actionSemaphore.release()
        self.log('onAction return')


    # Reset the sleep timer
    def startSleepTimer(self):
        if self.sleepTimeValue == 0:
            return

        # Cancel the timer if it is still running
        if self.sleepTimer.isAlive():
            self.sleepTimer.cancel()
            self.sleepTimer = threading.Timer(self.sleepTimeValue, self.sleepAction)

        if self.Player.stopped == False:
            self.sleepTimer.name = "SleepTimer"
            self.sleepTimer.start()


    def startNotificationTimer(self, timertime = NOTIFICATION_CHECK_TIME):
        self.log("startNotificationTimer")

        if self.notificationTimer.isAlive():
            self.notificationTimer.cancel()

        self.notificationTimer = threading.Timer(timertime, self.notificationAction)

        if self.Player.stopped == False:
            self.notificationTimer.name = "NotificationTimer"
            self.notificationTimer.start()


    # This is called when the sleep timer expires
    def sleepAction(self):
        self.log("sleepAction")
        self.actionSemaphore.acquire()
#        self.sleepTimer = threading.Timer(self.sleepTimeValue, self.sleepAction)
        # TODO: show some dialog, allow the user to cancel the sleep
        # perhaps modify the sleep time based on the current show
        self.end()


    # Run rules for a channel
    def runActions(self, action, channel, parameter):
        self.log("runActions " + str(action) + " on channel " + str(channel))

        if channel < 1:
            return

        self.runningActionChannel = channel
        index = 0

        for rule in self.channels[channel - 1].ruleList:
            if rule.actions & action > 0:
                self.runningActionId = index
                parameter = rule.runAction(action, self, parameter)

            index += 1

        self.runningActionChannel = 0
        self.runningActionId = 0
        return parameter


    def notificationAction(self):
        self.log("notificationAction")
        docheck = False

        if self.showNextItem == False:
            return

        if self.Player.isPlaying():
            if self.notificationLastChannel != self.currentChannel:
                docheck = True
            else:
                if self.notificationLastShow != xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition():
                    docheck = True
                else:
                    if self.notificationShowedNotif == False:
                        docheck = True

            if docheck == True:
                self.notificationLastChannel = self.currentChannel
                self.notificationLastShow = xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition()
                self.notificationShowedNotif = False

                if self.hideShortItems:
                    # Don't show any notification if the current show is < 60 seconds
                    if self.channels[self.currentChannel - 1].getItemDuration(self.notificationLastShow) < self.shortItemLength:
                        self.notificationShowedNotif = True

                timedif = self.channels[self.currentChannel - 1].getItemDuration(self.notificationLastShow) - self.Player.getTime()

                if self.notificationShowedNotif == False and timedif < NOTIFICATION_TIME_BEFORE_END and timedif > NOTIFICATION_DISPLAY_TIME:
                    nextshow = self.channels[self.currentChannel - 1].fixPlaylistIndex(self.notificationLastShow + 1)

                    if self.hideShortItems:
                        # Find the next show that is >= 60 seconds long
                        while nextshow != self.notificationLastShow:
                            if self.channels[self.currentChannel - 1].getItemDuration(nextshow) >= self.shortItemLength:
                                break
                            nextshow = self.channels[self.currentChannel - 1].fixPlaylistIndex(nextshow + 1)
                    
                    self.log('notification.init')     
                    mediapath = uni(self.channels[self.currentChannel - 1].getItemFilename(nextshow))         
                    self.logDebug('notification.mediapath.1 = ' + uni(mediapath))                        
                    self.logDebug('notification.MEDIA_LOC = ' + uni(MEDIA_LOC))     
                    chtype = int(ADDON_SETTINGS.getSetting('Channel_' + str(self.currentChannel - 1) + '_type'))
                    title = 'Coming Up Next'   
                    thumb = (DEFAULT_IMAGES_LOC + 'icon.png')
                
                    if REAL_SETTINGS.getSetting("ColorOverlay") == "true":
                        ChannelLogo = (self.channelLogos + ascii(self.channels[self.currentChannel - 1].name) + '_c.png')
                    else:
                        ChannelLogo = (self.channelLogos + ascii(self.channels[self.currentChannel - 1].name) + '.png')
                
                    if chtype <= 7:
                        type = {}
                        type['0'] = 'poster'
                        type['1'] = 'fanart' 
                        type['2'] = 'landscape'        
                        type['3'] = 'logo'       
                        type['4'] = 'clearart'             
                        
                        type = type[REAL_SETTINGS.getSetting('ComingUpArtwork')]
                        self.logDebug('notification.type = ' + str(type))    
                        jpg = ['banner', 'fanart', 'folder', 'landscape', 'poster']
                        png = ['character', 'clearart', 'logo']    
                        
                        if type in jpg:
                            type1EXT = (type + '.jpg')
                        else:
                            type1EXT = (type + '.png')
                        self.logDebug('notification.type.ext = ' + str(type1EXT))  

                        mediapathSeason, filename = os.path.split(mediapath)                        
                        mediapathSeries = os.path.dirname(mediapathSeason)
                        
                        mediapathSeries1 = ascii(os.path.join(mediapathSeries, type1EXT))
                        mediapathSeason1 = ascii(os.path.join(mediapathSeason, type1EXT))
                        
                        self.logDebug('notification.type.mediapathSeries = ' + uni(mediapathSeries1))
                        self.logDebug('notification.type.mediapathSeason = ' + uni(mediapathSeason1))  

                        if xbmcvfs.exists(mediapathSeries1):
                            thumb = mediapathSeries1
                        elif xbmcvfs.exists(mediapathSeason1):
                            thumb = mediapathSeason1
                        elif xbmcvfs.exists(ChannelLogo):
                            thumb = ChannelLogo
                        else: 
                            thumb = (DEFAULT_IMAGES_LOC + 'icon.png')

                    elif chtype >= 8:
                    
                        if xbmcvfs.exists(ChannelLogo):
                            thumb = ChannelLogo
                        elif mediapathSeason[0:6] == 'plugin':
                            id = mediapathSeason
                            id = id.replace("/?path=/root", "")
                            id = id.split('plugin://', 1)[-1]
                            id = 'special://home/addons/'+ id + '/icon.png'
                            self.log("notification.plugin.id = " + id)
                            thumb = id
                        else: 
                            thumb = (DEFAULT_IMAGES_LOC + 'icon.png')          
                    else: 
                        thumb = (DEFAULT_IMAGES_LOC + 'icon.png')
                    
                    xbmc.executebuiltin('XBMC.Notification(%s, %s, %s, %s)' % (title, self.channels[self.currentChannel - 1].getItemTitle(nextshow).replace(',', ''), str(NOTIFICATION_DISPLAY_TIME * 1000), thumb))
                    self.logDebug("thumb = " + str(thumb))
                    self.notificationShowedNotif = True

        self.startNotificationTimer()


    def playerTimerAction(self):
        self.playerTimer = threading.Timer(2.0, self.playerTimerAction)    
        chtype = (ADDON_SETTINGS.getSetting('Channel_' + str(self.currentChannel - 1) + '_type'))

        if self.Player.isPlaying():
            self.lastPlayTime = int(self.Player.getTime())
            self.lastPlaylistPosition = xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition()
            self.notPlayingCount = 0
        else:
            self.notPlayingCount += 1
            self.log("Adding to notPlayingCount")
        
        if self.channels[self.currentChannel - 1].getCurrentFilename()[-4:].lower() != 'strm' or chtype <= 7:
            if self.notPlayingCount >= 3:
                self.log("exiting after three peat error")
                self.end()
                return
        else:
            if self.notPlayingCount >= 3:
                self.log("switching after three peat error")
                #Change channel todo
                return          
                
        if self.Player.stopped == False:
            self.playerTimer.name = "PlayerTimer"
            self.playerTimer.start()


    def LiveTVArtDownloader(self, imdbid, tvdbid, type, type1, type1EXT, type2, type2EXT):
        self.log('LiveTVArtDownloader')
        tvdb = True
        imdb = True
        Artpath = xbmc.translatePath(os.path.join(ART_LOC))
        self.logDebug('LiveTVArtDownloader.Artpath = ' + uni(Artpath))
        
        if tvdbid == 'NA' or tvdbid == 0:
            tvdb = False
        
        if imdbid == 'NA' or imdbid == 0:
            imdb = False
        
        if tvdb and type == 'tvshow':  
            self.log('LiveTVArtDownloader.TV')
            print tvdbid
            fanartTV = fanarttv.FTV_TVProvider()
            URLLST = fanartTV.get_image_list(tvdbid)
            print URLLST
            if URLLST:
                URLLST = str(URLLST)
                URLLST = URLLST.split("{'art_type': ")
                print URLLST
                try:
                    Art1 = [s for s in URLLST if type1 in s]
                    Art1 = Art1[0]
                    Art1 = Art1[Art1.find("'url': '")+len("'url': '"):Art1.rfind("',")]
                    Art1 = Art1.split("',")[0]
                    fle1 = tvdbid + '-' + type1EXT
                    flename1 = xbmc.translatePath(os.path.join(ART_LOC) + fle1)
                    
                    if FileAccess.exists(flename1):
                        self.getControl(508).setImage(flename1)
                    else:
                        if not os.path.exists(os.path.join(Artpath)):
                            os.makedirs(os.path.join(Artpath))
                        
                        resource = urllib.urlopen(Art1)
                        output = open(flename1,"wb")
                        output.write(resource.read())
                        output.close()
                        self.getControl(508).setImage(flename1)
                except:
                    self.getControl(508).setImage(MEDIA_LOC + type1 + '.png')
                    pass
                
                try:
                    Art2 = [s for s in URLLST if type2 in s]
                    Art2 = Art2[0]
                    Art2 = Art2[Art2.find("'url': '")+len("'url': '"):Art2.rfind("',")]
                    Art2 = Art2.split("',")[0]
                    fle2 = tvdbid + '-' + type2EXT
                    flename2 = xbmc.translatePath(os.path.join(ART_LOC) + fle2)
                    
                    if FileAccess.exists(flename2):
                        self.getControl(510).setImage(flename2)
                    else:
                        if not os.path.exists(os.path.join(Artpath)):
                            os.makedirs(os.path.join(Artpath))
                        
                        resource = urllib.urlopen(Art2)
                        output = open(flename2,"wb")
                        output.write(resource.read())
                        output.close()
                        self.getControl(510).setImage(flename2)
                except:
                    self.getControl(510).setImage(MEDIA_LOC + type2 + '.png')
                    pass

            else:#fallback all artwork because there is no id
                self.getControl(508).setImage(MEDIA_LOC + type1 + '.png')
                self.getControl(510).setImage(MEDIA_LOC + type2 + '.png')
                
        elif imdb and type == 'movie':
            self.log('LiveTVArtDownloader.Movie')    
            fanartTV = fanarttv.FTV_MovieProvider()
            URLLST = fanartTV.get_image_list(imdbid)
            print URLLST
            if URLLST:
                URLLST = str(URLLST)
                URLLST = URLLST.split("{'art_type': ")
                try:#Type1 Art
                    Art1 = [s for s in URLLST if type1 in s]
                    print Art1
                    Art1 = Art1[0]
                    print Art1
                    Art1 = Art1[Art1.find("'url': '")+len("'url': '"):Art1.rfind("',")]
                    print Art1
                    Art1 = Art1.split("',")[0]      
                    print Art1          
                    fle1 = imdbid + '-' + type1EXT
                    flename1 = xbmc.translatePath(os.path.join(ART_LOC) + fle1) 
                    if FileAccess.exists(flename1):
                        self.getControl(508).setImage(flename1)
                    else:
                        if not os.path.exists(os.path.join(Artpath)):
                            os.makedirs(os.path.join(Artpath))
                    
                        resource = urllib.urlopen(Art1)# Replace with urlretrieve todo
                        output = open(flename1,"wb")
                        output.write(resource.read())
                        output.close()
                        self.getControl(508).setImage(flename1)
                except:
                    self.getControl(508).setImage(MEDIA_LOC + type1 + '.png')
                    pass
                
                try:#Type2 Art
                    Art2 = [s for s in URLLST if type2 in s]
                    print Art2
                    Art2 = Art2[0]
                    print Art2
                    Art2 = Art2[Art2.find("'url': '")+len("'url': '"):Art2.rfind("',")]
                    print Art2
                    Art2 = Art2.split("',")[0]
                    print Art2
                    fle2 = imdbid + '-' + type2EXT
                    flename2 = xbmc.translatePath(os.path.join(ART_LOC) + fle2)
                    
                    if FileAccess.exists(flename2):
                        self.getControl(510).setImage(flename2)
                    else:
                        if not os.path.exists(os.path.join(Artpath)):
                            os.makedirs(os.path.join(Artpath))
                        
                        resource = urllib.urlopen(Art2)# Replace with urlretrieve todo
                        output = open(flename2,"wb")
                        output.write(resource.read())
                        output.close()
                        self.getControl(510).setImage(flename2)  
                except:
                    self.getControl(510).setImage(MEDIA_LOC + type2 + '.png')
                    pass
            else:
                self.getControl(508).setImage(MEDIA_LOC + type1 + '.png')
                self.getControl(510).setImage(MEDIA_LOC + type2 + '.png')         
        else:
            self.getControl(508).setImage(MEDIA_LOC + type1 + '.png')
            self.getControl(510).setImage(MEDIA_LOC + type2 + '.png')            
            
    # cleanup and end
    def end(self):
        self.log('end')
        # Prevent the player from setting the sleep timer
        self.Player.stopped = True
        self.background.setVisible(True)
        curtime = time.time()
        xbmc.executebuiltin("PlayerControl(repeatoff)")
        self.isExiting = True
        updateDialog = xbmcgui.DialogProgress()
        updateDialog.create("PseudoTV Live", "Exiting")
        
        if CHANNEL_SHARING and self.isMaster:
            updateDialog.update(0, "Exiting", "Removing File Locks")
            GlobalFileLock.unlockFile('MasterLock')
        
        GlobalFileLock.close()

        if self.playerTimer.isAlive():
            self.playerTimer.cancel()
            self.playerTimer.join()

        if self.Player.isPlaying():
            self.lastPlayTime = self.Player.getTime()
            self.lastPlaylistPosition = xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition()
            self.Player.stop()

        updateDialog.update(1, "Exiting", "Stopping Threads")

        try:
            if self.channelLabelTimer.isAlive():
                self.channelLabelTimer.cancel()
                self.channelLabelTimer.join()
        except:
            pass

        updateDialog.update(2)

        try:
            if self.notificationTimer.isAlive():
                self.notificationTimer.cancel()
                self.notificationTimer.join()
        except:
            pass

        updateDialog.update(3)

        try:
            if self.infoTimer.isAlive():
                self.infoTimer.cancel()
                self.infoTimer.join()
        except:
            pass

        updateDialog.update(4)

        try:
            if self.sleepTimeValue > 0:
                if self.sleepTimer.isAlive():
                    self.sleepTimer.cancel()
        except:
            pass

        updateDialog.update(5)

        if self.channelThread.isAlive():
            for i in range(30):
                try:
                    self.channelThread.join(1.0)
                except:
                    pass

                if self.channelThread.isAlive() == False:
                    break

                updateDialog.update(6 + i, "Exiting", "Stopping Threads")

            if self.channelThread.isAlive():
                self.log("Problem joining channel thread", xbmc.LOGERROR)

        if self.isMaster:
            try:#Startup Channel
                SUPchannel = int(REAL_SETTINGS.getSetting('SUPchannel'))                
                if SUPchannel == 0:
                    REAL_SETTINGS.setSetting('CurrentChannel', str(self.currentChannel))    
            except:
                pass

            ADDON_SETTINGS.setSetting('LastExitTime', str(int(curtime)))

        if self.timeStarted > 0 and self.isMaster:
            updateDialog.update(35, "Exiting", "Saving Settings")
            validcount = 0

            for i in range(self.maxChannels):
                if self.channels[i].isValid:
                    validcount += 1
            
            if validcount > 0:
                incval = 65.0 / float(validcount)

                for i in range(self.maxChannels):
                    updateDialog.update(35 + int((incval * i)))

                    if self.channels[i].isValid:
                        if self.channels[i].mode & MODE_RESUME == 0:
                            ADDON_SETTINGS.setSetting('Channel_' + str(i + 1) + '_time', str(int(curtime - self.timeStarted + self.channels[i].totalTimePlayed)))
                            self.log("xxxxxx6 " + str(i))
                        else:
                            if i == self.currentChannel - 1:
                                # Determine pltime...the time it at the current playlist position
                                pltime = 0
                                self.log("position for current playlist is " + str(self.lastPlaylistPosition))

                                for pos in range(self.lastPlaylistPosition):
                                    pltime += self.channels[i].getItemDuration(pos)

                                ADDON_SETTINGS.setSetting('Channel_' + str(i + 1) + '_time', str(pltime + self.lastPlayTime))
                                
                            else:
                                tottime = 0

                                for j in range(self.channels[i].playlistPosition):
                                    tottime += self.channels[i].getItemDuration(j)

                                tottime += self.channels[i].showTimeOffset
                                ADDON_SETTINGS.setSetting('Channel_' + str(i + 1) + '_time', str(int(tottime)))
                               
                                
                self.storeFiles()

        updateDialog.close()
        self.background.setVisible(False)
        self.close()
