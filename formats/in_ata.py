
from art_import import ArtImporter

# import as white on black for ease of edit + export
DEFAULT_FG, DEFAULT_BG =113, 1
# most ATAs are 40 columns, but some are a couple chars longer and a few are 80!
WIDTH, HEIGHT = 80, 40

class ATAImporter(ArtImporter):
    format_name = 'ATASCII'
    format_description = """
ATARI 8-bit computer version of ASCII.
Imports with ATASCII character set and Atari palette.
    """
    allowed_file_extensions = ['ata']
    
    def run_import(self, in_filename, options={}):
        self.set_art_charset('atari')
        self.set_art_palette('atari')
        self.resize(WIDTH, HEIGHT)
        self.art.clear_frame_layer(0, 0, DEFAULT_BG)
        # iterate over the bytes
        data = open(in_filename, 'rb').read()
        i = 0
        x, y = 0, 0
        while i < len(data):
            fg, bg = DEFAULT_FG, DEFAULT_BG
            if x >= WIDTH:
                x = 0
                y += 1
            if y >= HEIGHT:
                y = HEIGHT - 1
            char = data[i]
            # handle control characters
            # (most not supported!)
            # https://en.wikipedia.org/wiki/ATASCII#Control_characters_2
            # tab
            if char == 127:
                x += 4
                i += 1
                continue
            # backspace
            elif char == 126:
                x -= 1
                i += 1
                continue
            # line break
            elif char == 155:
                x = 0
                y += 1
                i += 1
                continue
            # over 127: inverted set
            elif char > 127:
                fg, bg = DEFAULT_BG, DEFAULT_FG
                # being a little fancy here, redo if it backfires
                char -= 128
            # handle mismatch between playscii's version of ATASCII (which
            # prefers to have character index 0 empty) and the real ATASCII
            if char == 0:
                char = 32
            elif char == 32:
                char = 0
            self.art.set_tile_at(0, 0, x, y, char, fg, bg)
            x += 1
            i += 1
        return True
