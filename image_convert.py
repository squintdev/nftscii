
import math, os.path, time
import numpy as np

from PIL import Image, ImageChops, ImageStat

from renderable_sprite import SpriteRenderable
from lab_color import rgb_to_lab, lab_color_diff

"""
notes / future research

- generate a table of image sum vectors for every char/fg/bg combination in the charset + palette
- find the closest vector from this table for each source block
-- if it's a table, how to index?

- build dict of char index frequency, run each new source block comparison in order from most to least used (so far) characters.
-- takes same amount of time to try all characters, only helps if diffs use a "good-enough" threshold
-- threshold guesses: 0 = perfect, 600 = reasonable, 1000 = poor, 1500 crummy

"8088 corruption explained" talk:
https://www.youtube.com/watch?v=L6CkYou6hYU
- downsample each block bilinearly, divide each into 4x4 cells, then compare them with similarly bilinearly-downsampled char blocks
"""

class ImageConverter:
    
    tiles_per_tick = 1
    lab_color_comparison = True
    # delay in seconds before beginning to convert tiles.
    # lets eg UI catch up to BitmapImageImporter changes to Art.
    start_delay = 1.0
    
    def __init__(self, app, image_filename, art, bicubic_scale=False, sequence_converter=None):
        self.init_success = False
        image_filename = app.find_filename_path(image_filename)
        if not image_filename or not os.path.exists(image_filename):
            app.log("ImageConverter: Couldn't find image %s" % image_filename)
            app.converter = None
            return
        self.app = app
        self.start_time = time.time()
        self.image_filename = image_filename
        self.art = art
        self.finished = False
        # if an ImageSequenceConverter created us, keep a handle to it
        self.sequence_converter = sequence_converter
        try:
            self.src_img = Image.open(self.image_filename).convert('RGB')
        except:
            return
        # if we're part of a sequence, app doesn't need handle directly to us
        if not self.sequence_converter:
            self.app.converter = self
        # preserve aspect
        self.char_w, self.char_h = art.charset.char_width, art.charset.char_height
        art_pixel_w, art_pixel_h = self.char_w * art.width, self.char_h * art.height
        w, h = self.src_img.size
        ratio = min(art_pixel_h / h, art_pixel_w / w)
        w = math.floor((w * ratio) / self.char_w) * self.char_w
        h = math.floor((h * ratio) / self.char_h) * self.char_h
        scale_method = Image.BICUBIC if bicubic_scale else Image.NEAREST
        self.src_img = self.src_img.resize((w, h), resample=scale_method)
        # convert source image to art's palette
        self.src_img = self.art.palette.get_palettized_image(self.src_img)
        self.color_diffs = self.get_generated_color_diffs(self.art.palette.colors)
        # convert palettized source image to an array for fast comparisons
        self.src_array = np.fromstring(self.src_img.tobytes(), dtype=np.uint8)
        src_w, src_h = self.src_img.size
        self.src_array = np.reshape(self.src_array, (src_h, src_w))
        # convert charmap to 1-bit color for fast value swaps during
        # block comparison
        self.char_img = self.art.charset.image_data.copy().convert('RGB')
        bw_pal_img = Image.new('P', (1, 1))
        bw_pal = [0, 0, 0, 255, 255, 255]
        while len(bw_pal) < 256 * 3:
            bw_pal.append(0)
        bw_pal_img.putpalette(tuple(bw_pal))
        self.char_img = self.char_img.quantize(palette=bw_pal_img)
        self.char_array = np.fromstring(self.char_img.tobytes(), dtype=np.uint8)
        self.char_array = np.reshape(self.char_array, (self.art.charset.image_height, self.art.charset.image_width))
        # create, size and position image preview
        preview_img = self.src_img.copy()
        # remove transparency if source image is a GIF to avoid a PIL crash :[
        # TODO: https://github.com/python-pillow/Pillow/issues/1377
        if 'transparency' in preview_img.info:
            preview_img.info.pop('transparency')
        self.preview_sprite = SpriteRenderable(self.app, None, preview_img)
        # preview image scale takes into account character aspect
        self.preview_sprite.scale_x = w / (self.char_w / self.art.quad_width)
        self.preview_sprite.scale_y = h / (self.char_h / self.art.quad_height)
        # position in top left corner
        self.preview_sprite.y = -self.preview_sprite.scale_y
        self.preview_sprite.z = self.art.layers_z[self.art.active_layer] - 0.01
        # clear active layer so we can see preview
        self.art.clear_frame_layer(self.art.active_frame, self.art.active_layer, 0)
        # block indices
        self.x, self.y = 0, 0
        # precompute block indices - lets us do a bit less + and *
        self.char_blocks = []
        for char_y in range(self.art.charset.map_height):
            for char_x in range(self.art.charset.map_width):
                x0, y0 = char_x * self.char_w, char_y * self.char_h
                x1, y1 = x0 + self.char_w, y0 + self.char_h
                self.char_blocks.append((x0, y0, x1, y1))
                # characters might end mid-row, bail if so
                if len(self.char_blocks) > self.art.charset.last_index:
                    break
        self.init_success = True
    
    def get_generated_color_diffs(self, colors):
        # build table of color diffs
        unique_colors = len(colors)
        color_diffs = np.zeros((unique_colors, unique_colors), dtype=np.float32)
        # option: L*a*b color space conversion for greater accuracy
        get_color_diff = self.get_lab_color_diff if self.lab_color_comparison else self.get_rgb_color_diff
        #get_color_diff = self.get_nonlinear_rgb_color_diff
        for i,color in enumerate(colors):
            for j,other_color in enumerate(colors):
                color_diffs[i][j] = get_color_diff(color, other_color)
        return color_diffs
    
    def get_rgb_color_diff(self, color1, color2):
        r = abs(color1[0] - color2[0])
        g = abs(color1[1] - color2[1])
        b = abs(color1[2] - color2[2])
        a = abs(color1[3] - color2[3])
        return abs(r + g + b + a)
    
    def get_lab_color_diff(self, color1, color2):
        l1, a1, b1 = rgb_to_lab(*color1[:3])
        l2, a2, b2 = rgb_to_lab(*color2[:3])
        return lab_color_diff(l1, a1, b1, l2, a2, b2)
    
    def get_nonlinear_rgb_color_diff(self, color1, color2):
        # from http://www.compuphase.com/cmetric.htm
        rmean = int((color1[0] + color2[0]) / 2)
        r = color1[0] - color2[0]
        g = color1[1] - color2[1]
        b = color1[2] - color2[2]
        return math.sqrt((((512+rmean)*r*r)>>8) + 4*g*g + (((767-rmean)*b*b)>>8))
    
    def update(self):
        if time.time() < self.start_time + self.start_delay:
            return
        for i in range(self.tiles_per_tick):
            x_start, y_start = self.x * self.char_w, self.y * self.char_h
            x_end, y_end = x_start + self.char_w, y_start + self.char_h
            block = self.src_array[y_start:y_end, x_start:x_end]
            char, fg, bg = self.get_best_tile_for_block(block)
            # get_best_etc sometimes returns 0 for darkest blocks,
            # but transparency isn't properly supported yet
            fg = self.art.palette.darkest_index if fg == 0 else fg
            bg = self.art.palette.darkest_index if bg == 0 else bg
            self.art.set_tile_at(self.art.active_frame, self.art.active_layer,
                                 self.x, self.y, char, fg, bg)
            #print('set block %s,%s to ch %s fg %s bg %s' % (self.x, self.y, char, fg, bg))
            self.x += 1
            if self.x >= self.art.width:
                self.x = 0
                self.y += 1
                if self.y >= self.art.height:
                    self.finish()
                    break
    
    def get_color_combos_for_block(self, src_block):
        """
        returns # of unique colors, AND
        list of unique (fg, bg) color index tuples for given block
        """
        # get unique colors in source block
        colors, counts = np.unique(src_block, False, False, return_counts=True)
        # early out for single-color blocks
        if len(colors) == 1:
            return colors, []
        # sort by most to least used colors
        color_counts = []
        for i,color in enumerate(colors):
            color_counts += [(color, counts[i])]
        color_counts.sort(key=lambda item: item[1], reverse=True)
        combos = []
        for color1,count1 in color_counts:
            for color2,count2 in color_counts:
                if color1 == color2:
                    continue
                # fg/bg color swap SHOULD be allowed
                if (color1, color2) in combos:
                    continue
                combos.append((color1, color2))
        return colors, combos
    
    def get_best_tile_for_block(self, src_block):
        "returns a (char, fg, bg) tuple for the best match of given block"
        colors, combos = self.get_color_combos_for_block(src_block)
        # single color block?
        if len(combos) == 0:
            # if a block is all transparent pixels or outside image bounds,
            # no unique colors will be found
            bg = 0 if len(colors) == 0 else colors[0]
            return (0, 0, bg)
        # compare all combos + chars w/ source block
        best_char = 0
        best_diff = 9999999999999
        best_fg, best_bg = 0, 0
        for bg,fg in combos:
            # reset char index before each run through charset
            char_index = 0
            char_array = self.char_array.copy()
            # replace 1-bit color of char image with fg and bg colors
            char_array[char_array == 0] = bg
            char_array[char_array == 1] = fg
            for (x0, y0, x1, y1) in self.char_blocks:
                char_block = char_array[y0:y1, x0:x1]
                # using array of difference values w/ fancy numpy indexing,
                # sum() it
                # (used to be get_block_diff(block1, block2))
                diff = self.color_diffs[src_block, char_block].sum()
                # no difference = return immediately
                if diff == 0:
                    return (char_index, fg, bg)
                if diff < best_diff:
                    best_diff = diff
                    best_char = char_index
                    best_fg, best_bg = fg, bg
                    #print('%s is new best char index, diff %s:' % (char_index, diff))
                char_index += 1
        # return best (least different to source block) char/fg/bg found
        #print('%s is best char index, diff %s:' % (best_char, best_diff))
        return (best_char, best_fg, best_bg)
    
    def print_block(self, block, fg, bg):
        "prints ASCII representation of a block with . and # as white and black"
        w, h = block.shape
        s = ''
        for y in range(h):
            for x in range(w):
                if block[y][x] == fg:
                    s += '#'
                else:
                    s += '.'
            s += '\n'
        print(s)
    
    def finish(self, cancelled=False):
        self.finished = True
        if not self.sequence_converter:
            time_taken = time.time() - self.start_time
            verb = 'cancelled' if cancelled else 'finished'
            self.app.log('Conversion of image %s %s after %.3f seconds' % (self.image_filename, verb, time_taken))
            self.app.converter = None
        self.preview_sprite = None
        self.app.update_window_title()
