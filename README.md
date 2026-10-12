## 1.Voice-Controlled 2D Platformer (Python + Pygame)

Run, jump, collect coins and dodge spikes and slimes using your voice.
The keyboard always works as a backup.

1. Open the terminal: **Terminal -> New Terminal** (Ctrl + `).
2. Create a virtual environment:
   ```
   python -m venv venv
   venv\Scripts\activate
   ```
   - If PowerShell says scripts are disabled, run
     `Set-ExecutionPolicy -Scope Process Bypass` and activate again.
   - If VS Code asks to use the new environment, click **Yes**.
3. Install the libraries:
   ```
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
4. Run the game:
   ```
   python voice_platformer.py
   ```
   (or open the file and press the **Run** button / F5).

## 2. Microphone setup

- Windows Settings -> System -> Sound -> Input: pick your microphone.
- Settings -> Privacy & security -> Microphone: allow desktop apps to use it.
- Speech recognition uses Google's free web service, so you need internet.
- If recognition is poor, change `VOICE_LANGUAGE` at the top of the file
  (for example `"en-IN"` or `"en-GB"`).

## 3. Controls

| Action        | Voice                 | Keyboard              |
|---------------|-----------------------|-----------------------|
| Run left      | "left" / "move left"  | A or Left arrow       |
| Run right     | "right" / "move right"| D or Right arrow      |
| Jump          | "jump"                | Space, W or Up        |
| Stop          | "stop"                | S or Down             |
| Pause/resume  | "pause" / "resume"    | P or Esc              |
| Restart       | "restart"             | R                     |
| Start game    | "start"               | Enter                 |
| Mute mic      | -                     | M                     |

Voice "left"/"right" keep you running until you say "stop".
A jump said slightly early is remembered for a moment, so you can say it
a bit ahead of a gap.

## 4. How to play

- Collect coins (10 points each); squash slimes by landing on them (50 points).
- Spikes and slimes cost a life; falling in a pit costs a life and respawns
  you at the last green checkpoint flag.
- You have 5 lives. Reach the red GOAL flag to win (bonus points for lives left).

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| `pip install PyAudio` fails | Use Python 3.10-3.12. Or try `pip install pipwin` then `pipwin install pyaudio`. |
| HUD says "Mic error: no microphone found" | Plug in / enable a mic; the game retries every 5 seconds. Use the keyboard meanwhile. |
| HUD says "Offline: no internet" | Voice needs internet; keyboard still works. |
| Commands are missed | Speak one clear word, a little louder; avoid background noise. |

## Code map (`voice_platformer.py`)

- `VoiceController` - background thread: microphone -> text -> command queue
- `parse_command()` - turns "move left please" into `LEFT`
- `Level` - platforms, coins, spikes, slimes, checkpoints, goal
- `Player` - gravity, smooth movement, jumping, collisions
- `Game` - menu/pause/game-over/win screens, HUD, drawing, main loop
