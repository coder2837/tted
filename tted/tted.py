import multiprocessing
import threading
import time
import hashlib
import queue
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.styles import Style

def text_analyzer_worker(in_queue, out_queue):
    """
    CPU-bound worker process for text analysis.
    
    We run this in a completely separate process to avoid Python's GIL. 
    This ensures the main terminal UI thread stays buttery smooth and doesn't 
    stutter, even if the user pastes a massive 100MB log file into the editor.
    """
    while True:
        text = in_queue.get()
        if text is None:  # Poison pill to gracefully shut down the worker
            break
            
        # Simulating a heavy workload here to prove the UI doesn't freeze.
        # In a real app, this might be a syntax linter or autosave routine.
        word_count = len(text.split())
        text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]
        
        # Ship the computed stats back over the IPC boundary
        out_queue.put({'words': word_count, 'hash': text_hash})


def background_clock_thread(app, state):
    """
    Daemon thread to tick the clock in the status bar.
    
    prompt_toolkit operates on an event loop that usually only redraws when 
    the user presses a key. We use app.invalidate() here to force a screen 
    refresh every second so our clock actually ticks.
    """
    while not state['exit']:
        state['time'] = time.strftime("%H:%M:%S")
        app.invalidate()
        time.sleep(1)


def queue_listener_thread(app, out_queue, state):
    """
    Daemon thread that polls the IPC queue for worker results.
    
    Updates the shared state dictionary and triggers a UI redraw when new stats 
    arrive from the multiprocessing worker.
    """
    while not state['exit']:
        try:
            # We use a short timeout so this thread doesn't hang indefinitely 
            # when the user hits Ctrl+X and we need to tear down the app.
            result = out_queue.get(timeout=0.5) 
            state['words'] = result['words']
            state['hash'] = result['hash']
            app.invalidate()
        except queue.Empty:
            continue


def main():
    """
    Bootstraps the TUI layout, wires up event listeners, and manages the 
    lifecycles of our background processes and threads.
    """
    # Set up IPC queues for passing text buffer data back and forth
    in_queue = multiprocessing.Queue()
    out_queue = multiprocessing.Queue()
    
    # Spin up the background worker process
    worker_proc = multiprocessing.Process(target=text_analyzer_worker, args=(in_queue, out_queue))
    worker_proc.start()

    # Shared state dict. Threads will write to this, and the UI will read from it.
    app_state = {
        'time': '00:00:00',
        'words': 0,
        'hash': 'None',
        'exit': False
    }

    def on_text_changed(buffer):
        """Fires on every keystroke. Shoves the new buffer state into the worker queue."""
        in_queue.put(buffer.text)

    # Initialize the main text input widget
    text_area = TextArea(
        text="",
        scrollbar=True,
        line_numbers=True,
    )
    text_area.buffer.on_text_changed += on_text_changed

    bindings = KeyBindings()

    @bindings.add('c-x')
    def exit_app(event):
        """
        Clean teardown handler. 
        Kills the background threads via the state flag, poisons the worker process, 
        and exits the prompt_toolkit event loop.
        """
        app_state['exit'] = True
        in_queue.put(None)
        worker_proc.join(timeout=1)
        event.app.exit()

    def get_status_bar_text():
        """Generates the dynamic status bar string on every render pass."""
        # Grab zero-indexed cursor positions and bump them for humans
        row = text_area.document.cursor_position_row + 1
        col = text_area.document.cursor_position_col + 1
        
        w = app_state['words']
        h = app_state['hash']
        t = app_state['time']
        
        return [
            ('class:status', f' Ln: {row} Col: {col} | Words: {w} | SHA: {h} | Time: {t} | Ctrl-X to Exit ')
        ]

    # Wire up the layout tree
    status_bar = Window(
        content=FormattedTextControl(get_status_bar_text),
        height=1,
        style='class:status'
    )

    root_container = HSplit([
        text_area,
        status_bar,
    ])
    layout = Layout(root_container)

    # High-contrast styling for the status bar
    style = Style.from_dict({
        'status': 'bg:#ffffff #000000',
    })

    app = Application(
        layout=layout,
        key_bindings=bindings,
        style=style,
        full_screen=True,
        mouse_support=True
    )

    # Kick off our UI updater threads (daemonized so they don't block exit)
    threading.Thread(target=background_clock_thread, args=(app, app_state), daemon=True).start()
    threading.Thread(target=queue_listener_thread, args=(app, out_queue, app_state), daemon=True).start()

    # Hand over control to the prompt_toolkit event loop
    app.run()

# Windows requires this guard for multiprocessing to fork correctly
if __name__ == '__main__':
    multiprocessing.freeze_support() # MUST add this before main() for compiled apps!
    main()