"""
VOICE-CONTROLLED 2D PLATFORMER
==============================
Control the hero with your voice (or the keyboard as a backup!).

Tech stack: Python + Pygame + SpeechRecognition + PyAudio
(PyAutoGUI is NOT needed - voice commands are fed straight into the game.)

VOICE COMMANDS
    "left" / "move left"    run left until you say "stop"
    "right" / "move right"  run right until you say "stop"
    "jump"                  jump (can be said while running)
    "stop"                  stop moving
    "pause" / "resume"      pause or continue
    "restart"               start the level again
    "start"                 start the game from the menu

KEYBOARD (backup)
    A / D or Arrow keys = move      SPACE / W / UP = jump     S / DOWN = stop
    P or ESC = pause                R = restart               M = mute/unmute mic
    ENTER = start

Note: speech recognition uses Google's free web service, so it needs internet.
Without internet (or a microphone) the game keeps working with the keyboard.
"""

import math
import queue
import random
import sys
import threading
import time

import pygame

# SpeechRecognition is optional at import time: if it is missing the game
# still runs in keyboard-only mode instead of crashing.
try:
    import speech_recognition as sr
except ImportError:
    sr = None


# =============================================================================
# SETTINGS  (beginners: feel free to tweak these numbers!)
# =============================================================================
WIDTH, HEIGHT = 960, 540
FPS = 60
TITLE = "Voice Platformer"

VOICE_LANGUAGE = "en-US"      # try "en-IN" or "en-GB" if recognition is poor

# Physics
GRAVITY = 0.6
MAX_FALL_SPEED = 14
MOVE_SPEED = 4.2
ACCELERATION = 0.5
JUMP_SPEED = -13.2
COYOTE_FRAMES = 8             # can still jump a moment after leaving a ledge
JUMP_BUFFER_FRAMES = 15       # a "jump" said slightly early still counts
INVINCIBLE_FRAMES = 90        # blink time after being hurt

# Gameplay
MAX_LIVES = 5
COIN_POINTS = 10
STOMP_POINTS = 50
LEVEL_WIDTH = 4000
GROUND_Y = 460

# Game states
MENU, PLAYING, PAUSED, GAME_OVER, WIN = "menu", "playing", "paused", "game_over", "win"

# Voice command words. Order matters: the first group with a matching word wins.
COMMAND_WORDS = [
    ("RESTART", {"restart", "reset", "again", "retry"}),
    ("PAUSE",   {"pause", "wait"}),
    ("RESUME",  {"resume", "continue", "unpause"}),
    ("STOP",    {"stop", "halt", "freeze"}),
    ("JUMP",    {"jump", "hop", "up", "leap"}),
    ("LEFT",    {"left"}),
    ("RIGHT",   {"right", "write", "ride"}),   # "write" is a common mis-hearing
    ("START",   {"start", "play", "begin"}),
]

# Colours
WHITE = (255, 255, 255)
BLACK = (20, 20, 35)
YELLOW = (255, 220, 60)
GREEN = (80, 220, 100)
RED = (240, 70, 70)
GREY = (150, 150, 160)
STATUS_COLORS = {"ok": GREEN, "busy": (255, 200, 60), "error": RED, "off": GREY}


# =============================================================================
# VOICE RECOGNITION  (runs in a background thread so the game never freezes)
# =============================================================================
def parse_command(text):
    """Turn recognised speech such as 'move left please' into 'LEFT' (or None)."""
    words = text.lower().replace("-", " ").split()
    for command, keywords in COMMAND_WORDS:
        if any(word in keywords for word in words):
            return command
    return None


class VoiceController:
    """Listens to the microphone and puts recognised commands in a queue."""

    def __init__(self):
        self.commands = queue.Queue()      # (command, spoken_text) pairs
        self.status = "Starting microphone..."
        self.kind = "busy"                 # ok / busy / error / off  (for the colour)
        self.last_heard = ""
        self.muted = False
        self._running = False
        self._pending = 0                  # how many phrases are being recognised
        self._lock = threading.Lock()
        self._error_text = ""
        self._error_until = 0.0
        self._device_index = self._find_microphone_index()

    def _find_microphone_index(self):
        """Pick the default USB/desktop microphone if available."""
        if sr is None:
            return None
        try:
            names = sr.Microphone.list_microphone_names()
        except Exception:
            return None
        if not names:
            return None
        return 0

    @staticmethod
    def _friendly_error(error):
        message = str(error)
        if "PyAudio" in message:
            return "Mic error: PyAudio not installed"
        if "Permission" in message or "access" in message.lower():
            return "Mic error: Windows mic access blocked"
        if "device" in message.lower() or "input" in message.lower() or isinstance(error, OSError):
            return "Mic error: no microphone found"
        return "Mic error: " + message[:22]

    # ----- public ------------------------------------------------------------
    def start(self):
        self._running = True
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def stop(self):
        self._running = False

    def toggle_mute(self):
        self.muted = not self.muted

    # ----- internals ---------------------------------------------------------
    def _set(self, text, kind):
        self.status, self.kind = text, kind

    def _show_error(self, text, seconds=3.0):
        self._error_text = text
        self._error_until = time.time() + seconds
        self._update_listening_status()

    def _update_listening_status(self):
        if self.muted:
            self._set("Muted (press M to unmute)", "off")
        elif time.time() < self._error_until:
            self._set(self._error_text, "error")
        elif self._pending > 0:
            self._set("Listening... (recognizing)", "busy")
        else:
            self._set("Listening... speak now", "ok")

    def _listen_loop(self):
        """Open the mic, calibrate, then listen forever. Retries if the mic fails."""
        if sr is None:
            self._set("SpeechRecognition not installed", "error")
            return

        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 0.5          # short pause = end of command
        recognizer.non_speaking_duration = 0.3
        recognizer.dynamic_energy_threshold = True

        while self._running:
            try:
                mic = sr.Microphone(device_index=self._device_index)
                with mic as source:   # needs PyAudio + a microphone
                    self._set("Calibrating mic... stay quiet", "busy")
                    recognizer.adjust_for_ambient_noise(source, duration=1)
                    while self._running:
                        self._update_listening_status()
                        if self.muted:
                            time.sleep(0.2)
                            continue
                        try:
                            audio = recognizer.listen(source, timeout=1, phrase_time_limit=3)
                        except sr.WaitTimeoutError:
                            continue              # nobody spoke - just try again
                        # Recognise in a separate thread so we can keep listening.
                        threading.Thread(target=self._recognize,
                                         args=(recognizer, audio), daemon=True).start()
            except Exception as error:            # PyAudio missing, no mic, mic unplugged...
                self._set(self._friendly_error(error), "error")
                for _ in range(50):               # wait ~5 s, then try again
                    if not self._running:
                        return
                    time.sleep(0.1)

    def _recognize(self, recognizer, audio):
        """Send the audio to Google, translate it into a command, queue it."""
        with self._lock:
            self._pending += 1
        try:
            text = recognizer.recognize_google(audio, language=VOICE_LANGUAGE)
            command = parse_command(text)
            if command:
                self.last_heard = text
                self.commands.put((command, text))
            else:
                self.last_heard = text + "  (not a command)"
        except sr.UnknownValueError:
            self.last_heard = "(couldn't understand)"
        except sr.RequestError:
            self._show_error("Offline: no internet (use keys)")
        except Exception as error:                # never let a thread crash the game
            self._show_error("Voice error: " + str(error)[:20])
        finally:
            with self._lock:
                self._pending -= 1
            self._update_listening_status()


# =============================================================================
# PIXEL ART HELPERS
# =============================================================================
def make_sprite(rows, palette, scale):
    """Build a Surface from a list of text rows. Each letter is a palette colour."""
    surf = pygame.Surface((len(rows[0]) * scale, len(rows) * scale), pygame.SRCALPHA)
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char in palette:
                pygame.draw.rect(surf, palette[char], (x * scale, y * scale, scale, scale))
    return surf


PLAYER_PALETTE = {"R": (220, 50, 50), "S": (255, 205, 160), "B": (50, 100, 220),
                  "K": (30, 30, 40), "Y": (255, 220, 60), "N": (110, 60, 30)}
_HEAD = ["...RRRR...", "..RRRRRR..", "..SSSSSS..", "..SKSSKS..", "..SSSSSS..", "..SSSSSS.."]
PLAYER_IDLE = _HEAD + [".BBBBBBBB.", "SBBYBBYBBS", ".BBBBBBBB.",
                       ".BBB..BBB.", ".NNN..NNN.", ".NNN..NNN."]
PLAYER_STEP = _HEAD + [".BBBBBBBB.", "SBBYBBYBBS", ".BBBBBBBB.",
                       "..BBBBBB..", "..NNNNNN..", "..NNNNNN.."]
PLAYER_JUMP = _HEAD + ["SBBBBBBBBS", ".BBYBBYBB.", ".BBBBBBBB.",
                       ".BBB..BBB.", ".NNN..NNN.", ".NN....NN."]

COIN_PALETTE = {"Y": (255, 210, 40), "O": (220, 150, 20), "W": (255, 250, 180)}
COIN_ROWS = ["..YYYY..", ".YWWYYO.", "YWYYYYYO", "YWYYYYYO",
             "YYYYYYYO", "YYYYYYOO", ".YOOOOO.", "..OOOO.."]

SLIME_PALETTE = {"G": (90, 200, 90), "D": (50, 140, 60), "W": WHITE, "K": (30, 30, 40)}
SLIME_ROWS = ["....GGGG....", "..GGGGGGGG..", ".GGGGGGGGGG.", ".GWKGGGWKGG.",
              "GGGGGGGGGGGG", "GGGGGGGGGGGG", "GGGGGGGGGGGG", ".DDDDDDDDDD."]

HEART_ROWS = [".RR.RR.", "RRRRRRR", "RRRRRRR", ".RRRRR.", "..RRR..", "...R..."]


def load_sprites():
    """Create every sprite once at start-up."""
    flip = lambda s: pygame.transform.flip(s, True, False)
    sprites = {}
    for name, rows in (("idle", PLAYER_IDLE), ("step", PLAYER_STEP), ("jump", PLAYER_JUMP)):
        img = make_sprite(rows, PLAYER_PALETTE, 3)
        sprites["player_" + name] = (img, flip(img))       # (facing right, facing left)
    sprites["coin"] = make_sprite(COIN_ROWS, COIN_PALETTE, 3)
    sprites["slime"] = make_sprite(SLIME_ROWS, SLIME_PALETTE, 3)
    sprites["heart"] = make_sprite(HEART_ROWS, {"R": (240, 60, 80)}, 4)
    sprites["heart_empty"] = make_sprite(HEART_ROWS, {"R": (70, 70, 90)}, 4)
    return sprites


def make_ground_surface(w, h):
    """Grassy dirt block with random pixel specks."""
    surf = pygame.Surface((w, h))
    surf.fill((134, 88, 52))
    rng = random.Random(w * 7 + h)
    for _ in range(max(1, w * h // 150)):
        x = rng.randrange(0, max(1, w - 6)) // 6 * 6
        y = rng.randrange(14, max(15, h - 6)) // 6 * 6
        pygame.draw.rect(surf, rng.choice([(116, 74, 42), (150, 102, 62), (100, 64, 38)]),
                         (x, y, 6, 6))
    pygame.draw.rect(surf, (76, 175, 80), (0, 0, w, 14))
    pygame.draw.rect(surf, (129, 215, 98), (0, 0, w, 5))
    for x in range(0, w, 16):
        pygame.draw.rect(surf, (56, 142, 60), (x, 10, 8, 8))
    return surf


FLOAT_THEMES = [  # (top colour, body colour, line colour)
    ((255, 215, 100), (235, 120, 40), (170, 80, 30)),
    ((210, 160, 255), (120, 70, 190), (80, 45, 140)),
    ((140, 240, 235), (40, 150, 170), (25, 100, 120)),
]


def make_floating_surface(w, h, theme):
    """Colourful brick platform."""
    top, body, line = theme
    surf = pygame.Surface((w, h))
    surf.fill(body)
    for row, y in enumerate(range(0, h, 8)):
        pygame.draw.line(surf, line, (0, y), (w, y), 2)
        for x in range(4 if row % 2 else 16, w, 24):
            pygame.draw.line(surf, line, (x, y), (x, y + 8), 2)
    pygame.draw.rect(surf, top, (0, 0, w, 5))
    pygame.draw.rect(surf, line, (0, 0, w, h), 2)
    return surf


# =============================================================================
# LEVEL
# =============================================================================
class Slime:
    """A patrolling enemy. Jump on its head to squash it!"""

    def __init__(self, x_min, x_max):
        self.x_min, self.x_max = x_min, x_max - 36
        self.fx = float(x_min)
        self.rect = pygame.Rect(x_min, GROUND_Y - 24, 36, 24)
        self.direction = random.choice((-1, 1))
        self.alive = True

    def update(self):
        self.fx += self.direction * 1.2
        if self.fx <= self.x_min:
            self.fx, self.direction = self.x_min, 1
        elif self.fx >= self.x_max:
            self.fx, self.direction = self.x_max, -1
        self.rect.x = int(self.fx)


class Level:
    """Holds everything in the world: platforms, coins, spikes, enemies, flags."""

    def __init__(self):
        # Solid ground pieces (x, width). The gaps between them are pits!
        ground_parts = [(0, 700), (820, 500), (1440, 600), (2160, 700), (2980, 1020)]
        self.solids, self.solid_images = [], []
        for x, w in ground_parts:
            rect = pygame.Rect(x, GROUND_Y, w, 100)
            self.solids.append(rect)
            self.solid_images.append(make_ground_surface(w, 100))

        # Floating platforms (x, y, width). You can jump up through them.
        floating = [(330, 370, 130), (560, 300, 120), (720, 420, 80),
                    (900, 360, 140), (1110, 290, 120),
                    (1500, 360, 130), (1700, 300, 140), (1900, 370, 100),
                    (2250, 360, 120), (2450, 290, 120), (2650, 360, 140),
                    (3100, 360, 120), (3300, 300, 120), (3500, 360, 140)]
        self.oneways, self.oneway_images = [], []
        for i, (x, y, w) in enumerate(floating):
            self.oneways.append(pygame.Rect(x, y, w, 16))
            self.oneway_images.append(make_floating_surface(w, 16, FLOAT_THEMES[i % 3]))

        # Spikes (x, how many 36px blocks wide)
        self.spikes = [pygame.Rect(x, GROUND_Y - 22, 36 * n, 22)
                       for x, n in [(480, 1), (1050, 2), (1750, 1),
                                    (2400, 1), (3150, 1), (3650, 2)]]

        # Enemies
        self.slimes = [Slime(a, b) for a, b in
                       [(860, 1020), (1470, 1710), (2200, 2380), (2480, 2700), (3250, 3620)]]

        # Checkpoints (x position on the ground) - you respawn at the last one
        self.checkpoints = [1220, 1940, 2790]

        # Goal flag
        self.goal = pygame.Rect(3900, GROUND_Y - 120, 30, 120)

        # Coins
        self.coins = []
        self._place_coins()

    # --- coin helpers ---
    def _row(self, x, y, count, gap=36):
        for i in range(count):
            self.coins.append(pygame.Rect(x + i * gap, y, 24, 24))

    def _arc(self, x, y, count=5, gap=32, rise=55):
        for i in range(count):
            t = i / (count - 1)
            self.coins.append(pygame.Rect(x + i * gap, int(y - rise * 4 * t * (1 - t)), 24, 24))

    def _place_coins(self):
        self._row(150, 424, 3)
        self._arc(440, 410, 5, 30, 50)            # over first spike
        self._row(345, 330, 3)
        self._row(580, 250, 3)
        self._row(735, 380, 2)
        self._row(880, 424, 2)
        self._row(920, 320, 3)
        self._arc(1030, 400, 5, 35, 60)           # over double spike
        self._row(1130, 250, 3)
        self._arc(1310, 400, 5, 34, 60)           # over pit
        self._row(1520, 320, 3)
        self._row(1720, 260, 3)
        self._row(1910, 330, 2)
        self._arc(2030, 400, 5, 34, 60)           # over pit
        self._row(2270, 320, 3)
        self._row(2470, 250, 3)
        self._arc(2380, 410, 5, 30, 50)
        self._row(2670, 320, 3)
        self._arc(2850, 400, 5, 34, 60)           # over pit
        self._row(3120, 320, 3)
        self._row(3320, 260, 3)
        self._row(3520, 320, 3)
        self._arc(3640, 410, 5, 30, 50)
        self._row(3790, 424, 3)


# =============================================================================
# PLAYER
# =============================================================================
class Player:
    """The hero: smooth movement, gravity, jumping and platform collision."""
    WIDTH, HEIGHT = 24, 36

    def __init__(self, x, y):
        self.rect = pygame.Rect(x, y, self.WIDTH, self.HEIGHT)
        self.fx, self.fy = float(x), float(y)     # float position = smooth motion
        self.vx = self.vy = 0.0
        self.on_ground = False
        self.facing = 1
        self.coyote = 0
        self.jump_buffer = 0
        self.invincible = 0
        self.anim = 0.0

    def request_jump(self):
        self.jump_buffer = JUMP_BUFFER_FRAMES

    def respawn(self, x, y):
        self.fx, self.fy = float(x), float(y)
        self.rect.topleft = (x, y)
        self.vx = self.vy = 0.0
        self.invincible = INVINCIBLE_FRAMES

    def hurt(self):
        self.invincible = INVINCIBLE_FRAMES
        self.vy = -8
        self.vx = -self.facing * 5

    def update(self, move_dir, solids, oneways):
        """Advance one frame. move_dir is -1 (left), 0 (stop) or 1 (right)."""
        # Smooth acceleration towards the wanted speed
        target = move_dir * MOVE_SPEED
        if self.vx < target:
            self.vx = min(self.vx + ACCELERATION, target)
        elif self.vx > target:
            self.vx = max(self.vx - ACCELERATION, target)
        if move_dir:
            self.facing = move_dir

        # Timers + jumping (coyote time and jump buffer make voice control forgiving)
        self.coyote = COYOTE_FRAMES if self.on_ground else max(0, self.coyote - 1)
        self.jump_buffer = max(0, self.jump_buffer - 1)
        self.invincible = max(0, self.invincible - 1)
        if self.jump_buffer > 0 and self.coyote > 0:
            self.vy = JUMP_SPEED
            self.jump_buffer = self.coyote = 0
            self.on_ground = False

        self.vy = min(self.vy + GRAVITY, MAX_FALL_SPEED)

        # --- move horizontally, then fix collisions with solid ground ---
        self.fx += self.vx
        self.rect.x = round(self.fx)
        for block in solids:
            if self.rect.colliderect(block):
                if self.vx > 0:
                    self.rect.right = block.left
                elif self.vx < 0:
                    self.rect.left = block.right
                self.fx, self.vx = float(self.rect.x), 0.0
        if self.rect.left < 0:                       # level edges
            self.rect.left, self.fx, self.vx = 0, 0.0, 0.0
        if self.rect.right > LEVEL_WIDTH:
            self.rect.right = LEVEL_WIDTH
            self.fx, self.vx = float(self.rect.x), 0.0

        # --- move vertically, then fix collisions ---
        previous_bottom = self.rect.bottom
        self.fy += self.vy
        self.rect.y = round(self.fy)
        self.on_ground = False
        for block in solids:
            if self.rect.colliderect(block):
                if self.vy > 0:
                    self.rect.bottom = block.top
                    self.on_ground = True
                elif self.vy < 0:
                    self.rect.top = block.bottom
                self.fy, self.vy = float(self.rect.y), 0.0
        if self.vy >= 0:                              # one-way platforms: land from above only
            for plat in oneways:
                if self.rect.colliderect(plat) and previous_bottom <= plat.top:
                    self.rect.bottom = plat.top
                    self.fy, self.vy = float(self.rect.y), 0.0
                    self.on_ground = True

        self.anim += abs(self.vx) * 0.05

    def sprite_key(self):
        if not self.on_ground:
            return "player_jump"
        if abs(self.vx) > 0.5 and int(self.anim * 2) % 2 == 1:
            return "player_step"
        return "player_idle"


# =============================================================================
# THE GAME
# =============================================================================
class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption(TITLE)
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        names = "consolas,couriernew,lucidaconsole,monospace"
        self.font_s = pygame.font.SysFont(names, 16, bold=True)
        self.font_m = pygame.font.SysFont(names, 22, bold=True)
        self.font_l = pygame.font.SysFont(names, 40, bold=True)
        self.font_title = pygame.font.SysFont(names, 14, bold=True)

        self.sprites = load_sprites()
        self.sky = self._build_sky()
        self.clouds = [(random.randrange(0, 1400), random.randrange(30, 190), random.choice((3, 4, 5)))
                       for _ in range(9)]
        self.menu_ground = make_ground_surface(WIDTH, 100)

        self.voice = VoiceController()
        self.voice.start()

        self.running = True
        self.state = MENU
        self.frame = 0
        self.last_command = "-"
        self.last_command_time = 0
        self.reset_level()

    # ------------------------------------------------------------------ setup
    def reset_level(self):
        """Create a fresh level and player (used for start and restart)."""
        self.level = Level()
        self.player = Player(60, GROUND_Y - Player.HEIGHT)
        self.respawn_x = 60
        self.score = 0
        self.lives = MAX_LIVES
        self.coins_collected = 0
        self.voice_dir = 0          # direction set by voice ("left"/"right"/"stop")
        self.move_dir = 0
        self.camx = 0.0
        self.particles = []

    def _build_sky(self):
        sky = pygame.Surface((WIDTH, HEIGHT))
        top, bottom, bands = (90, 165, 250), (205, 235, 255), 18
        for i in range(bands):
            t = i / (bands - 1)
            color = tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3))
            pygame.draw.rect(sky, color, (0, i * HEIGHT // bands, WIDTH, HEIGHT // bands + 1))
        pygame.draw.circle(sky, (255, 240, 170), (800, 90), 52)
        pygame.draw.circle(sky, (255, 250, 210), (800, 90), 40)
        return sky

    def start_game(self):
        self.reset_level()
        self.state = PLAYING

    # ----------------------------------------------------------------- inputs
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self.handle_key(event.key)

    def handle_key(self, key):
        """Keyboard backup controls."""
        if key == pygame.K_m:
            self.voice.toggle_mute()
        if self.state == MENU:
            if key in (pygame.K_RETURN, pygame.K_SPACE):
                self.start_game()
            elif key == pygame.K_ESCAPE:
                self.running = False
        elif self.state == PLAYING:
            if key in (pygame.K_SPACE, pygame.K_w, pygame.K_UP):
                self.player.request_jump()
            elif key in (pygame.K_s, pygame.K_DOWN):
                self.voice_dir = 0
            elif key in (pygame.K_p, pygame.K_ESCAPE):
                self.state = PAUSED
            elif key == pygame.K_r:
                self.start_game()
        elif self.state == PAUSED:
            if key in (pygame.K_p, pygame.K_ESCAPE, pygame.K_RETURN):
                self.state = PLAYING
            elif key == pygame.K_r:
                self.start_game()
        else:  # GAME_OVER or WIN
            if key in (pygame.K_r, pygame.K_RETURN):
                self.start_game()
            elif key == pygame.K_ESCAPE:
                self.state = MENU

    def process_voice_commands(self):
        """Take every command the microphone thread queued and apply it."""
        while True:
            try:
                command, _text = self.voice.commands.get_nowait()
            except queue.Empty:
                break
            self.last_command = command
            self.last_command_time = pygame.time.get_ticks()
            self.apply_command(command)

    def apply_command(self, command):
        """React to a voice command depending on the current game state."""
        if self.state == MENU:
            if command == "START":
                self.start_game()
        elif self.state == PLAYING:
            if command == "LEFT":
                self.voice_dir = -1
            elif command == "RIGHT":
                self.voice_dir = 1
            elif command == "STOP":
                self.voice_dir = 0
            elif command == "JUMP":
                self.player.request_jump()
            elif command == "PAUSE":
                self.state = PAUSED
            elif command == "RESTART":
                self.start_game()
        elif self.state == PAUSED:
            if command in ("RESUME", "START", "PAUSE"):
                self.state = PLAYING
            elif command == "RESTART":
                self.start_game()
        else:  # GAME_OVER / WIN
            if command in ("RESTART", "START"):
                self.start_game()

    # ----------------------------------------------------------------- update
    def update_playing(self):
        player, level = self.player, self.level

        # Keyboard direction overrides voice while a key is held
        keys = pygame.key.get_pressed()
        key_dir = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (keys[pygame.K_LEFT] or keys[pygame.K_a])
        if key_dir:
            self.voice_dir = 0
        self.move_dir = key_dir or self.voice_dir

        player.update(self.move_dir, level.solids, level.oneways)

        # Coins
        for coin in level.coins[:]:
            if player.rect.colliderect(coin):
                level.coins.remove(coin)
                self.score += COIN_POINTS
                self.coins_collected += 1
                self.spawn_particles(coin.centerx, coin.centery, (255, 220, 60))

        # Enemies: stomp from above, otherwise ouch
        for slime in level.slimes:
            if not slime.alive:
                continue
            slime.update()
            if player.rect.colliderect(slime.rect):
                if player.vy > 1 and player.rect.bottom - slime.rect.top < 20:
                    slime.alive = False
                    player.vy = -9
                    self.score += STOMP_POINTS
                    self.spawn_particles(slime.rect.centerx, slime.rect.centery, (90, 200, 90))
                elif player.invincible == 0:
                    self.damage_player()
        level.slimes = [s for s in level.slimes if s.alive]

        # Spikes
        if player.invincible == 0:
            for spike in level.spikes:
                if player.rect.colliderect(spike.inflate(-8, -10)):
                    self.damage_player()
                    break

        # Checkpoints
        for cp_x in level.checkpoints:
            if player.rect.centerx >= cp_x and cp_x > self.respawn_x:
                self.respawn_x = cp_x

        # Fell into a pit
        if player.rect.top > HEIGHT + 60:
            self.lose_life()
            if self.state == PLAYING:
                player.respawn(self.respawn_x, GROUND_Y - Player.HEIGHT - 2)
                self.voice_dir = 0

        # Reached the goal flag
        if player.rect.colliderect(level.goal):
            self.score += self.lives * 100          # bonus for remaining lives
            self.state = WIN

        # Camera follows the player smoothly
        target = player.rect.centerx - WIDTH * 0.4
        target = max(0, min(LEVEL_WIDTH - WIDTH, target))
        self.camx += (target - self.camx) * 0.12

    def damage_player(self):
        self.player.hurt()
        self.lose_life()

    def lose_life(self):
        self.lives -= 1
        if self.lives <= 0:
            self.lives = 0
            self.state = GAME_OVER

    def spawn_particles(self, x, y, color):
        for _ in range(8):
            self.particles.append([x, y, random.uniform(-3, 3), random.uniform(-5, -1), 30, color])

    def update_particles(self):
        for p in self.particles:
            p[0] += p[2]
            p[1] += p[3]
            p[3] += 0.3
            p[4] -= 1
        self.particles = [p for p in self.particles if p[4] > 0]

    # ---------------------------------------------------------------- drawing
    def draw_text(self, text, font, color, x, y, align="left"):
        img = font.render(text, False, color)
        shadow = font.render(text, False, BLACK)
        w = img.get_width()
        if align == "center":
            x -= w // 2
        elif align == "right":
            x -= w
        self.screen.blit(shadow, (x + 2, y + 2))
        self.screen.blit(img, (x, y))

    def draw_pixel_title(self, text, color, y, scale=4):
        """Render small text then scale it up for a chunky pixel look."""
        img = self.font_title.render(text, False, color)
        shadow = self.font_title.render(text, False, BLACK)
        size = (img.get_width() * scale, img.get_height() * scale)
        img, shadow = pygame.transform.scale(img, size), pygame.transform.scale(shadow, size)
        x = WIDTH // 2 - size[0] // 2
        self.screen.blit(shadow, (x + scale, y + scale))
        self.screen.blit(img, (x, y))

    def draw_panel(self, rect, alpha=150):
        panel = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
        panel.fill((15, 15, 35, alpha))
        self.screen.blit(panel, (rect[0], rect[1]))
        pygame.draw.rect(self.screen, (255, 255, 255), rect, 2)

    def draw_background(self, camx):
        self.screen.blit(self.sky, (0, 0))
        for cx, cy, s in self.clouds:           # slow parallax clouds
            x = (cx - camx * 0.15) % (WIDTH + 300) - 150
            for rx, ry, rw, rh in ((0, 16, 24, 8), (6, 8, 12, 8), (12, 0, 8, 8), (4, 14, 16, 4)):
                pygame.draw.rect(self.screen, WHITE, (x + rx * s // 2, cy + ry * s // 2,
                                                      rw * s // 2, rh * s // 2))
        for factor, base, amp, color, seed in ((0.25, 350, 40, (140, 205, 170), 0.0),
                                                (0.5, 405, 26, (95, 175, 125), 2.0)):
            for sx in range(0, WIDTH + 8, 8):   # blocky parallax hills
                wx = sx + camx * factor
                h = base + amp * math.sin(wx / 170 + seed) + amp * 0.5 * math.sin(wx / 63 + seed * 2)
                top = int(h) // 8 * 8
                pygame.draw.rect(self.screen, color, (sx, top, 8, HEIGHT - top))

    def draw_flag(self, x, base_y, height, color):
        pygame.draw.rect(self.screen, (200, 200, 210), (x, base_y - height, 4, height))
        pygame.draw.rect(self.screen, YELLOW, (x - 2, base_y - height - 4, 8, 8))
        wave = int(3 * math.sin(self.frame * 0.12))
        top = base_y - height + 4
        points = [(x + 4, top), (x + 4 + 34, top + 12 + wave), (x + 4, top + 24)]
        pygame.draw.polygon(self.screen, color, points)

    def draw_world(self):
        cx = int(self.camx)
        level, screen = self.level, self.screen
        self.draw_background(self.camx)

        def visible(rect):
            return rect.right > cx and rect.left < cx + WIDTH

        for rect, image in zip(level.solids, level.solid_images):
            if visible(rect):
                screen.blit(image, (rect.x - cx, rect.y))
        for rect, image in zip(level.oneways, level.oneway_images):
            if visible(rect):
                screen.blit(image, (rect.x - cx, rect.y))

        # Flags
        for cp_x in level.checkpoints:
            active = self.respawn_x >= cp_x
            self.draw_flag(cp_x - cx, GROUND_Y, 70, GREEN if active else GREY)
        self.draw_flag(level.goal.x - cx, GROUND_Y, 120, RED)
        self.draw_text("GOAL", self.font_s, WHITE, level.goal.x - cx + 2, GROUND_Y - 150)

        # Spikes
        for spike in level.spikes:
            if visible(spike):
                for i in range(spike.w // 18):
                    x = spike.x - cx + i * 18
                    tip = [(x, spike.bottom), (x + 9, spike.top), (x + 18, spike.bottom)]
                    pygame.draw.polygon(screen, (205, 210, 225), tip)
                    pygame.draw.polygon(screen, (120, 125, 145), tip, 2)
                    pygame.draw.line(screen, WHITE, (x + 9, spike.top + 4), (x + 5, spike.bottom - 4), 2)

        # Coins (bobbing + spinning)
        coin_img = self.sprites["coin"]
        for coin in level.coins:
            if visible(coin):
                width = max(4, int(24 * abs(math.cos(self.frame * 0.08 + coin.x * 0.01))))
                img = pygame.transform.scale(coin_img, (width, 24))
                bob = int(3 * math.sin(self.frame * 0.1 + coin.x * 0.05))
                screen.blit(img, (coin.centerx - width // 2 - cx, coin.y + bob))

        # Slimes (squishy)
        for slime in level.slimes:
            if visible(slime.rect):
                squish = int(3 * math.sin(self.frame * 0.15 + slime.rect.x * 0.1))
                img = pygame.transform.scale(self.sprites["slime"], (36, 24 + squish))
                screen.blit(img, (slime.rect.x - cx, slime.rect.bottom - img.get_height()))

        # Player (blinks while invincible)
        player = self.player
        if not (player.invincible > 0 and (player.invincible // 5) % 2 == 0):
            right_img, left_img = self.sprites[player.sprite_key()]
            screen.blit(right_img if player.facing > 0 else left_img,
                        (player.rect.x - 3 - cx, player.rect.y))

        for x, y, _vx, _vy, life, color in self.particles:
            pygame.draw.rect(screen, color, (int(x) - cx, int(y), 5, 5))

    def status_text(self):
        if self.state == MENU:
            return "Main menu"
        if self.state == PAUSED:
            return "Paused"
        if self.state == GAME_OVER:
            return "Game over"
        if self.state == WIN:
            return "You win!"
        if not self.player.on_ground:
            action = "jumping"
        elif self.move_dir:
            action = "running " + ("right" if self.move_dir > 0 else "left")
        else:
            action = "standing"
        return "Playing - " + action

    def draw_mic_status(self, x, y):
        """Coloured dot + microphone status + last heard phrase + last command."""
        voice = self.voice
        pygame.draw.circle(self.screen, STATUS_COLORS[voice.kind], (x + 8, y + 9), 7)
        pygame.draw.circle(self.screen, WHITE, (x + 8, y + 9), 7, 2)
        self.draw_text("MIC " + voice.status, self.font_s, WHITE, x + 24, y)
        heard = voice.last_heard or "-"
        self.draw_text("Heard: " + heard[:30], self.font_s, (200, 220, 255), x, y + 24)
        recent = pygame.time.get_ticks() - self.last_command_time < 800
        self.draw_text("Command: " + self.last_command, self.font_s,
                       YELLOW if recent else WHITE, x, y + 46)

    def draw_hud(self):
        total = self.coins_collected + len(self.level.coins)
        self.draw_panel((10, 10, 250, 92))
        self.draw_text(f"SCORE  {self.score:05d}", self.font_m, YELLOW, 22, 18)
        self.draw_text(f"COINS  {self.coins_collected}/{total}", self.font_s, WHITE, 22, 48)
        for i in range(MAX_LIVES):
            heart = self.sprites["heart"] if i < self.lives else self.sprites["heart_empty"]
            self.screen.blit(heart, (22 + i * 36, 70))

        self.draw_panel((WIDTH - 400, 10, 390, 92))
        self.draw_mic_status(WIDTH - 388, 18)

        self.draw_panel((10, HEIGHT - 36, WIDTH - 20, 28))
        self.draw_text("STATUS: " + self.status_text(), self.font_s, GREEN, 20, HEIGHT - 31)
        self.draw_text("Say: LEFT RIGHT JUMP STOP PAUSE RESTART  |  M = mute mic",
                       self.font_s, WHITE, WIDTH - 20, HEIGHT - 31, "right")

    def draw_overlay(self, title, color, lines):
        dim = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        dim.fill((10, 10, 30, 170))
        self.screen.blit(dim, (0, 0))
        self.draw_pixel_title(title, color, 130, 6)
        for i, line in enumerate(lines):
            self.draw_text(line, self.font_m, WHITE, WIDTH // 2, 250 + i * 34, "center")

    def draw_menu(self):
        scroll = self.frame * 0.6
        self.draw_background(scroll)
        self.screen.blit(self.menu_ground, (0, GROUND_Y))
        self.screen.blit(self.sprites["player_idle"][0], (WIDTH // 2 - 15, GROUND_Y - 36))

        self.draw_pixel_title("VOICE PLATFORMER", YELLOW, 40, 5)
        self.draw_text("Run, jump and collect coins using only your voice!",
                       self.font_m, WHITE, WIDTH // 2, 130, "center")

        self.draw_panel((150, 175, 660, 190), 170)
        self.draw_text("VOICE", self.font_m, GREEN, 180, 187)
        self.draw_text("KEYBOARD (backup)", self.font_m, GREEN, 500, 187)
        voice_lines = ["LEFT / RIGHT : run", "JUMP : hop over things", "STOP : stand still",
                       "PAUSE / RESUME", "RESTART : try again"]
        key_lines = ["A D or arrows : run", "SPACE / W : jump", "S : stop",
                     "P : pause   R : restart", "M : mute microphone"]
        for i, (a, b) in enumerate(zip(voice_lines, key_lines)):
            self.draw_text(a, self.font_s, WHITE, 180, 222 + i * 26)
            self.draw_text(b, self.font_s, WHITE, 500, 222 + i * 26)

        self.draw_mic_status(150, 378)
        if (self.frame // 30) % 2 == 0:
            self.draw_text("Say  \"START\"  or press ENTER", self.font_m, YELLOW,
                           WIDTH // 2, 438, "center")

    def draw(self):
        if self.state == MENU:
            self.draw_menu()
        else:
            self.draw_world()
            self.draw_hud()
            if self.state == PAUSED:
                self.draw_overlay("PAUSED", YELLOW, [
                    "Say RESUME  or press P", "Say RESTART  or press R"])
            elif self.state == GAME_OVER:
                self.draw_overlay("GAME OVER", RED, [
                    f"Final score: {self.score}",
                    "Say RESTART  or press R", "ESC = main menu"])
            elif self.state == WIN:
                self.draw_overlay("YOU WIN!", GREEN, [
                    f"Final score: {self.score}   (coins: {self.coins_collected})",
                    "Say RESTART  or press R", "ESC = main menu"])
        pygame.display.flip()

    # -------------------------------------------------------------- main loop
    def run(self):
        while self.running:
            self.handle_events()
            self.process_voice_commands()
            if self.state == PLAYING:
                self.update_playing()
                self.update_particles()
            self.draw()
            self.frame += 1
            self.clock.tick(FPS)
        self.voice.stop()
        pygame.quit()


def main():
    Game().run()
    sys.exit()


if __name__ == "__main__":
    main()
