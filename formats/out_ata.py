
from art_export import ArtExporter
from art import TileIter

class ANSExporter(ArtExporter):
    format_name = 'ATASCII'
    format_description = """
ATARI 8-bit computer version of ASCII.
Assumes ATASCII character set and Atari palette.
Any tile with non-black background will be considered inverted.
    """
    file_extension = 'ata'
    
    def run_export(self, out_filename, options):
        # binary file; encoding into ANSI bytes happens just before write
        self.outfile = open(out_filename, 'wb')
        for frame, layer, x, y in TileIter(self.art):
            # only read from layer 0 of frame 0
            if layer > 0 or frame > 0:
                continue
            ch, fg, bg, _ = self.art.get_tile_at(frame, layer, x, y)
            # swap char indices 0 (heart) and 32 (blank space)
            if ch == 0:
                ch = 32
            elif ch == 32:
                ch = 0
            # non-black (color 1) background = use inverted set
            if bg > 1:
                ch += 128
            self.outfile.write(bytes([ch]))
            # line break
            if x == self.art.width - 1:
                self.outfile.write(bytes([155]))
        self.outfile.close()
        return True
