
import numpy as np

from image_convert import ImageConverter
from ui_dialog import UIDialog, Field, SkipFieldType
from formats.in_bitmap import BitmapImageImporter, ConvertImageChooserDialog, ConvertImageOptionsDialog


class TwoColorConvertImageOptionsDialog(ConvertImageOptionsDialog):
    # simplified version of parent options dialog, reusing as much as possible
    title = 'Convert 2-color bitmap image options'
    field5_label = ConvertImageOptionsDialog.field5_label
    field6_label = ConvertImageOptionsDialog.field6_label
    field7_label = ConvertImageOptionsDialog.field7_label
    field8_label = ConvertImageOptionsDialog.field8_label
    field10_label = ConvertImageOptionsDialog.field10_label
    field_width = ConvertImageOptionsDialog.field_width
    fields = [
        Field(label='', type=SkipFieldType, width=0, oneline=True),
        Field(label='', type=SkipFieldType, width=0, oneline=True),
        Field(label='', type=SkipFieldType, width=0, oneline=True),
        Field(label='', type=SkipFieldType, width=0, oneline=True),
        Field(label='', type=SkipFieldType, width=0, oneline=True),
        Field(label=field5_label, type=None, width=0, oneline=True),
        Field(label=field6_label, type=bool, width=0, oneline=True),
        Field(label=field7_label, type=bool, width=0, oneline=True),
        Field(label=field8_label, type=float, width=field_width, oneline=True),
        Field(label='', type=None, width=0, oneline=True),
        Field(label=field10_label, type=bool, width=0, oneline=True),
        Field(label='', type=None, width=0, oneline=True)
    ]
    
    def __init__(self, ui, options):
        ConvertImageOptionsDialog.__init__(self, ui, options)
        self.active_field = 6
    
    def get_initial_field_text(self, field_number):
        # alternate defaults - use 1:1 scaling
        if field_number == 6:
            return ' '
        elif field_number == 7:
            return UIDialog.true_field_text
        elif field_number == 8:
            # % of source image size - alternate default
            return '100.0'
        else:
            return ConvertImageOptionsDialog.get_initial_field_text(self, field_number)


class TwoColorImageConverter(ImageConverter):
    
    def get_color_combos_for_block(self, src_block):
        colors, counts = np.unique(src_block, False, False, return_counts=True)
        if len(colors) > 0:
            return (1, 2), [(2, 1)]
        # handle blocks with 0 unique colors, ie clear tiles out of image bounds
        # (just return white background)
        else:
            return [2], []


class TwoColorBitmapImageImporter(BitmapImageImporter):
    format_name = '2-color bitmap image'
    format_description = """
Variation on bitmap image conversion that forces
a black and white (1-bit) palette, and doesn't use
fg/bg color swaps. Suitable for plaintext export.
    """
    file_chooser_dialog_class = ConvertImageChooserDialog
    options_dialog_class = TwoColorConvertImageOptionsDialog
    completes_instantly = False
    
    def run_import(self, in_filename, options={}):
        # force palette to 1-bit black and white
        palette = self.app.load_palette('bw')
        self.art.set_palette(palette)
        
        width, height = options['art_width'], options['art_height']
        self.art.resize(width, height) # Importer.init will adjust UI
        bicubic_scale = options['bicubic_scale']
        ic = TwoColorImageConverter(self.app, in_filename, self.art, bicubic_scale)
        # early failures: file no longer exists, PIL fails to load and convert image
        if not ic.init_success:
            return False
        self.app.update_window_title()
        return True
