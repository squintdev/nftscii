import sdl2

from ui_element import UIElement
from ui_dialog import UIDialog

class PagedInfoDialog(UIDialog):
    
    "dialog that presents multiple pages of info w/ buttons to navigate next/last page"
    
    title = 'Info'
    # message = list of page strings, each can be triple-quoted / contain line breaks
    message = ['']
    tile_width = 54
    confirm_caption = '>>'
    other_caption = '<<'
    cancel_caption = 'Done'
    other_button_visible = True
    extra_lines = 1
    
    def __init__(self, ui, options):
        self.page = 0
        UIDialog.__init__(self, ui, options)
        self.reset_art()
    
    def update(self):
        # disable prev/next buttons if we're at either end of the page list
        if self.page == 0:
            self.other_button.can_hover = False
            self.other_button.set_state('dimmed')
        elif self.page == len(self.message) - 1:
            self.confirm_button.can_hover = False
            self.confirm_button.set_state('dimmed')
        else:
            for button in [self.confirm_button, self.other_button]:
                button.can_hover = True
                button.dimmed = False
                if button.state != 'normal':
                    button.set_state('normal')
        UIElement.update(self)
    
    def handle_input(self, key, shift_pressed, alt_pressed, ctrl_pressed):
        keystr = sdl2.SDL_GetKeyName(key).decode()
        if keystr == 'Left':
            self.other_pressed()
        elif keystr == 'Right':
            self.confirm_pressed()
        elif keystr == 'Escape':
            self.cancel_pressed()
    
    def get_message(self):
        return self.message[self.page].rstrip().split('\n')
    
    def confirm_pressed(self):
        # confirm repurposed to "next page"
        if self.page < len(self.message) - 1:
            self.page += 1
            # redraw, tell reset_art not to resize
            self.reset_art(False)
    
    def cancel_pressed(self):
        self.dismiss()
    
    def other_pressed(self):
        # other repurposed to "previous page"
        if self.page > 0:
            self.page -= 1
            self.reset_art(False)


about_message = [
# max line width 50 characters!
"""
           Programming Contributions:

SquintDev

                Technical Advice:



            Tool Design Inspiration:


""",
"""
      Love, Encouragement, Moral Support:

TheseWoods

#tool-design
"""
]

class AboutDialog(PagedInfoDialog):
    title = 'NFTscii'
    message = about_message
    game_mode_visible = True
    all_modes_visible = True
    def __init__(self, ui, options):
        self.title += ' %s' % ui.app.version
        PagedInfoDialog.__init__(self, ui, options)
