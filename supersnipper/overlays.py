"""Tk overlays for region selection, armed-click targeting and the recording countdown."""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from PIL import Image, ImageGrab, ImageTk

from . import config, winapi


class SnipOverlay:
    def __init__(self, screenshot: Image.Image) -> None:
        import tkinter as tk
        from tkinter import messagebox

        self.tk = tk
        self.messagebox = messagebox
        self.screenshot = screenshot
        self.photo = None
        self.result: Optional[Tuple[int, int, int, int]] = None
        self.start: Optional[Tuple[int, int]] = None

        x, y, width, height = winapi.virtual_screen()
        self.root = tk.Tk()
        self.root.title("Super Snipper")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.root,
            cursor="crosshair",
            bg="black",
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        try:
            self.photo = ImageTk.PhotoImage(self.screenshot)
            self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        except Exception:
            logging.exception("Failed to render frozen screenshot background")
        self.rect = self.canvas.create_rectangle(0, 0, 0, 0, outline="#00d5ff", width=2)
        self.hint = self.canvas.create_text(
            18,
            18,
            anchor="nw",
            fill="white",
            text="Drag to snip. Esc cancels.",
            font=("Segoe UI", 14, "bold"),
        )

        self.root.bind("<Escape>", self.cancel)
        winapi._watch_capture_cancel(self.root, self.cancel)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

    def cancel(self, _event=None) -> None:
        self.result = None
        self.root.destroy()

    def on_press(self, event) -> None:
        self.start = (int(event.x), int(event.y))
        self.canvas.coords(self.rect, event.x, event.y, event.x, event.y)
        self.canvas.itemconfigure(self.hint, state="hidden")

    def on_drag(self, event) -> None:
        if not self.start:
            return
        x0, y0 = self.start
        self.canvas.coords(self.rect, x0, y0, event.x, event.y)

    def on_release(self, event) -> None:
        if not self.start:
            self.cancel()
            return

        x0, y0 = self.start
        x1, y1 = int(event.x), int(event.y)
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))

        if right - left < 4 or bottom - top < 4:
            self.cancel()
            return

        canvas_width = max(1, int(self.canvas.winfo_width()))
        canvas_height = max(1, int(self.canvas.winfo_height()))
        scale_x = self.screenshot.width / canvas_width
        scale_y = self.screenshot.height / canvas_height

        crop = (
            max(0, int(round(left * scale_x))),
            max(0, int(round(top * scale_y))),
            min(self.screenshot.width, int(round(right * scale_x))),
            min(self.screenshot.height, int(round(bottom * scale_y))),
        )
        self.result = crop
        self.root.destroy()

    def run(self) -> Optional[Tuple[int, int, int, int]]:
        self.root.focus_force()
        self.root.mainloop()
        return self.result


class ClickTargetOverlay:
    def __init__(self, screenshot: Image.Image) -> None:
        import tkinter as tk

        self.tk = tk
        self.screenshot = screenshot
        self.photo = None
        self.result: Optional[Tuple[int, int]] = None
        self.skipped = False

        x, y, width, height = winapi.virtual_screen()
        self.virtual_origin = (x, y)
        self.root = tk.Tk()
        self.root.title("Super Snipper Armed Click")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.root,
            cursor="crosshair",
            bg="black",
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        try:
            self.photo = ImageTk.PhotoImage(self.screenshot)
            self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        except Exception:
            logging.exception("Failed to render frozen screenshot background for armed click")
        self.canvas.create_text(
            18,
            18,
            anchor="nw",
            fill="white",
            text="Click target to arm start-click. Esc records without a click.",
            font=("Segoe UI", 14, "bold"),
        )

        self.root.bind("<Escape>", self.skip)
        winapi._watch_capture_cancel(self.root, self.skip)
        self.canvas.bind("<ButtonRelease-1>", self.on_click)

    def skip(self, _event=None) -> None:
        self.skipped = True
        self.result = None
        self.root.destroy()

    def on_click(self, event) -> None:
        canvas_width = max(1, int(self.canvas.winfo_width()))
        canvas_height = max(1, int(self.canvas.winfo_height()))
        scale_x = self.screenshot.width / canvas_width
        scale_y = self.screenshot.height / canvas_height
        vx, vy = self.virtual_origin
        x = vx + max(0, min(self.screenshot.width - 1, int(round(event.x * scale_x))))
        y = vy + max(0, min(self.screenshot.height - 1, int(round(event.y * scale_y))))
        self.result = (x, y)
        self.root.destroy()

    def run(self) -> Optional[Tuple[int, int]]:
        self.root.focus_force()
        self.root.mainloop()
        return self.result


class CountdownOverlay:
    def __init__(self, seconds: int, label: str = "Recording starts in") -> None:
        import tkinter as tk

        self.tk = tk
        self.seconds = max(0, int(seconds))
        self.label = label
        self.remaining = self.seconds
        self.root = tk.Tk()
        self.root.title("Super Snipper Countdown")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#10131a")
        self.root.geometry("+80+80")
        self.root.bind("<Escape>", self.cancel)
        self.cancelled = False
        winapi._watch_capture_cancel(self.root, self.cancel)

        self.frame = tk.Frame(self.root, bg="#10131a", padx=22, pady=16)
        self.frame.pack()
        self.title = tk.Label(
            self.frame,
            text=self.label,
            bg="#10131a",
            fg="#f5f7fb",
            font=("Segoe UI", 12, "bold"),
        )
        self.title.pack()
        self.value = tk.Label(
            self.frame,
            text=str(self.remaining),
            bg="#10131a",
            fg="#00d5ff",
            font=("Segoe UI", 34, "bold"),
        )
        self.value.pack()
        self.note = tk.Label(
            self.frame,
            text="Esc cancels",
            bg="#10131a",
            fg="#a6adbb",
            font=("Segoe UI", 9),
        )
        self.note.pack()

    def cancel(self, _event=None) -> None:
        self.cancelled = True
        self.root.destroy()

    def tick(self) -> None:
        if self.cancelled:
            return
        if self.remaining <= 0:
            self.root.destroy()
            return
        self.value.configure(text=str(self.remaining))
        self.remaining -= 1
        self.root.after(1000, self.tick)

    def run(self) -> bool:
        if self.seconds <= 0:
            return True
        self.root.focus_force()
        self.tick()
        self.root.mainloop()
        return not self.cancelled


def select_video_region() -> Optional[Tuple[int, int, int, int]]:
    winapi.set_dpi_awareness()
    screenshot = ImageGrab.grab(all_screens=True)
    logging.info(
        "Captured physical screen image for video region: %sx%s",
        screenshot.width,
        screenshot.height,
    )
    return SnipOverlay(screenshot).run()


def select_click_target() -> Optional[Tuple[int, int]]:
    winapi.set_dpi_awareness()
    screenshot = ImageGrab.grab(all_screens=True)
    logging.info(
        "Captured physical screen image for armed click target: %sx%s",
        screenshot.width,
        screenshot.height,
    )
    return ClickTargetOverlay(screenshot).run()


def show_recording_countdown(
    seconds: int = config.DEFAULT_RECORD_COUNTDOWN_SECONDS,
    armed_click: bool = False,
) -> bool:
    label = "Recording starts, then armed click fires" if armed_click else "Recording starts in"
    return CountdownOverlay(seconds, label=label).run()
