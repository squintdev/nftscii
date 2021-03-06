
import os, sys, math, time, importlib, json, traceback
from collections import namedtuple

import sdl2

import game_object, game_util_objects, game_hud, game_room
import collision, vector
from camera import Camera
from grid import GameGrid
from art import ART_DIR
from charset import CHARSET_DIR
from palette import PALETTE_DIR

TOP_GAME_DIR = 'games/'
DEFAULT_STATE_FILENAME = 'start'
STATE_FILE_EXTENSION = 'gs'
GAME_SCRIPTS_DIR = 'scripts/'
SOUNDS_DIR = 'sounds/'

# generic starter script with a GO and Player subclass
STARTER_SCRIPT = """
from game_object import GameObject
from game_util_objects import Player


class MyGamePlayer(Player):
    "Generic starter player class for newly created games."
    art_src = 'default_player'
    # no "move" state art, so just use stand state for now
    move_state = 'stand'


class MyGameObject(GameObject):
    "Generic starter object class for newly created games."
    def update(self):
        # write "hello" in a color that shifts over time
        color = self.art.palette.get_random_color_index()
        self.art.write_string(0, 0, 3, 2, 'hello!', color)
        # run parent class update
        GameObject.update(self)
"""


# Quickie class to debug render order
RenderItem = namedtuple('RenderItem', ['obj', 'layer', 'sort_value'])


class GameCamera(Camera):
    pan_friction = 0.2
    use_bounds = False

class GameWorld:
    """
    Holds global state for Game Mode. Spawns, manages, and renders GameObjects.
    Properties serialized via WorldPropertiesObject.
    Global state can be controlled via a WorldGlobalsObject.
    """
    game_title = 'Untitled Game'
    "Title for game, shown in window titlebar when not editing"
    gravity_x, gravity_y, gravity_z = 0., 0., 0.
    "Gravity applied to all objects who are affected by gravity."
    bg_color = [0., 0., 0., 1.]
    "OpenGL wiewport color to render behind everything else, ie the void."
    hud_class_name = 'GameHUD'
    "String name of HUD class to use"
    properties_object_class_name = 'WorldPropertiesObject'
    globals_object_class_name = 'WorldGlobalsObject'
    "String name of WorldGlobalsObject class to use."
    player_camera_lock = True
    "If True, camera will be locked to player's location."
    object_grid_snap = True
    # editable properties
    # TODO:
    #update_when_unfocused = False
    #"If True, game sim will update even when window doesn't have input focus"
    draw_hud = True
    allow_pause = True
    "If False, user cannot pause game sim"
    collision_enabled = True
    "If False, CollisionLord won't bother thinking about collision at all."
    # toggles for "show all" debug viz modes
    show_collision_all = False
    show_bounds_all = False
    show_origin_all = False
    show_all_rooms = False
    "If True, show all rooms not just current one."
    draw_debug_objects = True
    "If False, objects with is_debug=True won't be drawn."
    room_camera_changes_enabled = True
    "If True, snap camera to new room's associated camera marker."
    list_only_current_room_objects = False
    "If True, list UI will only show objects in current room."
    builtin_module_names = ['game_object', 'game_util_objects', 'game_hud',
                            'game_room']
    builtin_base_classes = (game_object.GameObject, game_hud.GameHUD,
                            game_room.GameRoom)
    
    def __init__(self, app):
        self.app = app
        "Application that created this world."
        self.game_dir = None
        "Currently loaded game directory."
        self.sounds_dir = None
        self.game_name = None
        "Game's internal name, based on its directory."
        self.selected_objects = []
        self.hovered_objects = []
        self.hovered_focus_object = None
        "Set by check_hovers(), to the object that will be selected if edit mode user clicks"
        self.last_click_on_ui = False
        self.last_mouse_click_x, self.last_mouse_click_y = 0, 0
        self.properties = None
        "Our WorldPropertiesObject"
        self.globals = None
        "Our WorldGlobalsObject - not required"
        self.camera = GameCamera(self.app)
        self.grid = GameGrid(self.app)
        self.grid.visible = False
        self.player = None
        self.paused = False
        self._pause_time = 0
        self.updates = 0
        "Number of updates this we have performed."
        self.modules = {'game_object': game_object,
                        'game_util_objects': game_util_objects,
                        'game_hud': game_hud, 'game_room': game_room}
        self.classname_to_spawn = None
        self.objects = {}
        "Dict of objects by name:object"
        self.new_objects = {}
        "Dict of just-spawned objects, added to above on update() after spawn"
        self.rooms = {}
        "Dict of rooms by name:room"
        self.current_room = None
        self.cl = collision.CollisionLord(self)
        self.hud = None
        self.art_loaded = []
        self.drag_objects = {}
        "Offsets for objects player is edit-dragging"
        self.last_state_loaded = DEFAULT_STATE_FILENAME
    
    def play_music(self, music_filename, fade_in_time=0):
        "Play given music file in any SDL2_mixer-supported format."
        music_filename = self.game_dir + SOUNDS_DIR + music_filename
        self.app.al.set_music(music_filename)
        self.app.al.start_music(music_filename)
    
    def pause_music(self):
        self.app.al.pause_music()
    
    def resume_music(self):
        self.app.al.resume_music()
    
    def stop_music(self):
        "Stop any currently playing music."
        self.app.al.stop_all_music()
    
    def is_music_playing(self):
        "Return True if there is music playing."
        return self.app.al.is_music_playing()
    
    def pick_next_object_at(self, x, y):
        "Return next unselected object at given point."
        objects = self.get_objects_at(x, y)
        # early out
        if len(objects) == 0:
            return None
        # don't bother cycling if only one object found
        if len(objects) == 1 and objects[0].selectable and \
           not objects[0] in self.selected_objects:
                return objects[0]
        # cycle through objects at point til an unselected one is found
        for obj in objects:
            if not obj.selectable:
                continue
            if obj in self.selected_objects:
                continue
            return obj
        return None
    
    def get_objects_at(self, x, y, allow_locked=False):
        "Return list of all objects whose bounds fall within given point."
        objects = []
        for obj in self.objects.values():
            if obj.locked and not allow_locked:
                continue
            # only allow selecting of visible objects
            # (can still be selected via list panel)
            if obj.visible and obj.is_point_inside(x, y):
                objects.append(obj)
        # sort objects in Z, highest first
        objects.sort(key=lambda obj: obj.z, reverse=True)
        return objects
    
    def select_click(self):
        x, y, z = vector.screen_to_world(self.app, self.app.mouse_x,
                                         self.app.mouse_y)
        # remember last place we clicked
        self.last_mouse_click_x, self.last_mouse_click_y = x, y
        if self.classname_to_spawn:
            new_obj = self.spawn_object_of_class(self.classname_to_spawn, x, y)
            if self.current_room:
                self.current_room.add_object(new_obj)
            self.app.ui.message_line.post_line('Spawned %s' % new_obj.name)
            return
        objects = self.get_objects_at(x, y)
        next_obj = self.pick_next_object_at(x, y)
        if len(objects) == 0:
            self.deselect_all()
        # ctrl: unselect first selected object found under mouse
        elif self.app.il.ctrl_pressed:
            for obj in self.selected_objects:
                if obj in objects:
                    self.deselect_object(obj)
                    break
        # shift: add to current selection
        elif self.app.il.shift_pressed:
            self.select_object(next_obj)
        # no mod keys: select only object under cursor, deselect all if none
        elif not next_obj and len(objects) == 0:
            self.deselect_all()
        # special case: must use shift-click for objects
        # beneath (lower Z) topmost
        elif next_obj is not objects[0]:
            pass
        else:
            self.select_object(next_obj)
        # remember object offsets from cursor for dragging
        for obj in self.selected_objects:
            self.drag_objects[obj.name] = (obj.x - x, obj.y - y, obj.z - z)
    
    def clicked(self, button):
        # if edit UI is up, select stuff
        if self.app.ui.is_game_edit_ui_visible():
            if button == sdl2.SDL_BUTTON_LEFT:
                self.select_click()
        # else pass clicks to any objects under mouse
        else:
            x, y, z = vector.screen_to_world(self.app, self.app.mouse_x,
                                             self.app.mouse_y)
            # 'locked" only relevant to edit mode, ignore it if in play mode
            objects = self.get_objects_at(x, y, allow_locked=True)
            for obj in objects:
                if obj.handle_mouse_events and \
                   (not obj.locked or not self.app.can_edit):
                    obj.clicked(button, x, y)
                    if obj.consume_mouse_events:
                        break
    
    def select_unclick(self):
        # clicks on UI are consumed and flag world to not accept unclicks
        # (keeps unclicks after dialog dismiss from deselecting objects)
        if self.last_click_on_ui:
            self.last_click_on_ui = False
            # clear drag objects, since we're leaving valid drag context
            # fixes unwanted drag after eg ESC exiting a menu
            self.drag_objects.clear()
            return
        # if we're clicking to spawn something, don't drag/select
        if self.classname_to_spawn:
            return
        x, y, z = vector.screen_to_world(self.app, self.app.mouse_x,
                                         self.app.mouse_y)
        # remember selected objects now, they might be deselected but still
        # need to have their collision turned back on.
        selected_objects = self.selected_objects[:]
        if len(self.selected_objects) > 0 and not self.app.il.shift_pressed:
            # if mouse has traveled much since click, deselect all objects
            # except one mouse is over.
            dx = self.last_mouse_click_x - x
            dy = self.last_mouse_click_y - y
            if math.sqrt(dx ** 2 + dy ** 2) < 1.5:
                for obj in self.get_objects_at(x, y):
                    if obj in self.selected_objects:
                        self.deselect_all()
                        self.select_object(obj)
                        break
        # end drag, forget drag object offsets
        self.drag_objects.clear()
        for obj in selected_objects:
            obj.enable_collision()
            if obj.collision_shape_type == collision.CST_TILE:
                obj.collision.create_shapes()
            obj.collision.update()
    
    def unclicked(self, button):
        if self.app.ui.is_game_edit_ui_visible():
            if button == sdl2.SDL_BUTTON_LEFT:
                self.select_unclick()
        else:
            x, y, z = vector.screen_to_world(self.app, self.app.mouse_x,
                                             self.app.mouse_y)
            objects = self.get_objects_at(x, y)
            for obj in objects:
                if obj.handle_mouse_events:
                    obj.unclicked(button, x, y)
    
    def check_hovers(self):
        "Update objects on their mouse hover status"
        x, y, z = vector.screen_to_world(self.app, self.app.mouse_x,
                                         self.app.mouse_y)
        new_hovers = self.get_objects_at(x, y)
        # if this object will be selected on left click; draw bounds & label
        if self.app.ui.is_game_edit_ui_visible():
            next_obj = self.pick_next_object_at(x, y)
            self.hovered_focus_object = next_obj
        # if in play mode, notify objects who have begun to be hovered
        else:
            for obj in new_hovers:
                if obj.handle_mouse_events and not obj in self.hovered_objects:
                    obj.hovered(x, y)
            # check for objects un-hovered by this move
            for obj in self.hovered_objects:
                if obj.handle_mouse_events and not obj in new_hovers:
                    obj.unhovered(x, y)
        self.hovered_objects = new_hovers
    
    def mouse_wheeled(self, wheel_y):
        x, y, z = vector.screen_to_world(self.app, self.app.mouse_x,
                                         self.app.mouse_y)
        objects = self.get_objects_at(x, y, allow_locked=True)
        for obj in objects:
            if obj.handle_mouse_events and \
               (not obj.locked or not self.app.can_edit):
                obj.mouse_wheeled(wheel_y)
                if obj.consume_mouse_events:
                    break
    
    def mouse_moved(self, dx, dy):
        if self.app.ui.active_dialog:
            return
        # bail if mouse didn't move (in world space - include camera) last input
        if self.app.mouse_dx == 0 and self.app.mouse_dy == 0 and \
           not self.camera.moved_this_frame:
            return
        # if last onclick was a UI element, don't drag
        if self.last_click_on_ui:
            return
        self.check_hovers()
        # not dragging anything?
        if len(self.selected_objects) == 0:
            return
        # 0-length drags cause unwanted snapping
        if dx == 0 and dy == 0:
            return
        # set dragged objects to mouse + offset from mouse when drag started
        x, y, z = vector.screen_to_world(self.app, self.app.mouse_x,
                                         self.app.mouse_y)
        for obj_name,offset in self.drag_objects.items():
            obj = self.objects[obj_name]
            if obj.locked:
                continue
            obj.disable_collision()
            obj.x = x + offset[0]
            obj.y = y + offset[1]
            obj.z = z + offset[2]
            if self.object_grid_snap:
                obj.x = round(obj.x)
                obj.y = round(obj.y)
                # if odd width/height, origin will be between quads and
                # edges will be off-grid; nudge so that edges are on-grid
                if obj.art.width % 2 != 0:
                    obj.x += obj.art.quad_width / 2
                if obj.art.height % 2 != 0:
                    obj.y += obj.art.quad_height / 2
    
    def select_object(self, obj, force=False):
        "Add given object to our list of selected objects."
        if not self.app.can_edit:
            return
        if obj and (obj.selectable or force) and not obj in self.selected_objects:
            self.selected_objects.append(obj)
        self.app.ui.object_selection_changed()
    
    def deselect_object(self, obj):
        "Remove given object from our list of selected objects."
        if obj in self.selected_objects:
            self.selected_objects.remove(obj)
        self.app.ui.object_selection_changed()
    
    def deselect_all(self):
        "Deselect all objects."
        self.selected_objects = []
        self.app.ui.object_selection_changed()
    
    def create_new_game(self, new_game_dir, new_game_title):
        "Create appropriate dirs and files for a new game, return success."
        self.unload_game()
        new_dir = self.app.documents_dir + TOP_GAME_DIR + new_game_dir + '/'
        if os.path.exists(new_dir):
            self.app.log('Game dir %s already exists!' % new_game_dir)
            return False
        os.mkdir(new_dir)
        os.mkdir(new_dir + ART_DIR)
        os.mkdir(new_dir + GAME_SCRIPTS_DIR)
        os.mkdir(new_dir + SOUNDS_DIR)
        os.mkdir(new_dir + CHARSET_DIR)
        os.mkdir(new_dir + PALETTE_DIR)
        # create a generic starter script with a GO and Player subclass
        f = open(new_dir + GAME_SCRIPTS_DIR + new_game_dir + '.py', 'w')
        f.write(STARTER_SCRIPT)
        f.close()
        # load game
        self.set_game_dir(new_game_dir)
        self.properties = self.spawn_object_of_class('WorldPropertiesObject')
        self.objects.update(self.new_objects)
        self.new_objects = {}
        # HACK: set some property defaults, no idea why they don't take :[
        self.collision_enabled = self.properties.collision_enabled = True
        self.game_title = self.properties.game_title = new_game_title
        self.save_to_file(DEFAULT_STATE_FILENAME)
        return True
    
    def unload_game(self):
        "Unload currently loaded game."
        for obj in self.objects.values():
            obj.destroy()
        self.cl.reset()
        self.camera.reset()
        self.player = None
        self.globals = None
        self.properties = None
        if self.hud:
            self.hud.destroy()
            self.hud = None
        self.objects, self.new_objects = {}, {}
        self.rooms = {}
        # art_loaded is cleared when game dir is set
        self.selected_objects = []
        self.app.al.stop_all_music()
    
    def get_first_object_of_type(self, class_name, allow_subclasses=True):
        "Return first object found with given class name."
        c = self.get_class_by_name(class_name)
        for obj in self.objects.values():
            # use isinstance so we catch subclasses
            if c and allow_subclasses:
                if isinstance(obj, c):
                    return obj
            elif type(obj).__name__ == class_name:
                return obj
    
    def get_all_objects_of_type(self, class_name, allow_subclasses=True):
        "Return list of all objects found with given class name."
        c = self.get_class_by_name(class_name)
        objects = []
        for obj in self.objects.values():
            if c and allow_subclasses:
                if isinstance(obj, c):
                    objects.append(obj)
            elif type(obj).__name__ == class_name:
                objects.append(obj)
        return objects
    
    def set_for_all_objects(self, name, value):
        "Set given variable name to given value for all objects."
        for obj in self.objects.values():
            setattr(obj, name, value)
    
    def edit_art_for_selected(self):
        if len(self.selected_objects) == 0:
            return
        self.app.exit_game_mode()
        for obj in self.selected_objects:
            for art_filename in obj.get_all_art():
                self.app.load_art_for_edit(art_filename)
    
    def move_selected(self, move_x, move_y, move_z):
        "Shift position of selected objects by given x,y,z amount."
        for obj in self.selected_objects:
            obj.x += move_x
            obj.y += move_y
            obj.z += move_z
    
    def reset_game(self):
        "Reset currently loaded game to last loaded state."
        if self.game_dir:
            self.load_game_state(self.last_state_loaded)
    
    def set_game_dir(self, dir_name, reset=False):
        "Load game from given game directory."
        if self.game_dir and dir_name == self.game_dir:
            self.load_game_state(DEFAULT_STATE_FILENAME)
            return
        # loading a new game, wipe art list
        self.art_loaded = []
        # check in user documents dir first
        game_dir = TOP_GAME_DIR + dir_name
        doc_game_dir = self.app.documents_dir + game_dir
        for d in [doc_game_dir, game_dir]:
            if not os.path.exists(d):
                continue
            self.game_dir = d
            self.game_name = dir_name
            if not d.endswith('/'):
                self.game_dir += '/'
            self.app.log('Game data folder is now %s' % self.game_dir)
            # set sounds dir before loading state; some obj inits depend on it
            self.sounds_dir = self.game_dir + SOUNDS_DIR
            if reset:
                # load in a default state, eg start.gs
                self.load_game_state(DEFAULT_STATE_FILENAME)
            else:
                # if no reset load submodules into namespace from the get-go
                self._import_all()
                self.classes = self._get_all_loaded_classes()
            break
        if not self.game_dir:
            self.app.log("Couldn't find game directory %s" % dir_name)
    
    def _remove_non_current_game_modules(self):
        """
        Remove modules from previously-loaded games from both sys and
        GameWorld's dicts.
        """
        modules_to_remove = []
        games_dir_prefix = TOP_GAME_DIR.replace('/', '')
        this_game_dir_prefix = '%s.%s' % (games_dir_prefix, self.game_name)
        for module_name in sys.modules:
            # remove any module that isn't for this game or part of its path
            if module_name != games_dir_prefix and \
               module_name != this_game_dir_prefix and \
               module_name.startswith(games_dir_prefix) and \
               not module_name.startswith(this_game_dir_prefix + '.'):
                modules_to_remove.append(module_name)
        for module_name in modules_to_remove:
            sys.modules.pop(module_name)
            if module_name in self.modules:
                self.modules.pop(module_name)
    
    def _get_game_modules_list(self):
        "Return list of current game's modules from its scripts/ dir"
        # build list of module files
        modules_list = self.builtin_module_names[:]
        # create appropriately-formatted python import path
        module_path_prefix = '%s.%s.%s.' % (TOP_GAME_DIR.replace('/', ''),
                                            self.game_name,
                                            GAME_SCRIPTS_DIR.replace('/', ''))
        for filename in os.listdir(self.game_dir + GAME_SCRIPTS_DIR):
            # exclude emacs temp files and special world start script
            if not filename.endswith('.py'):
                continue
            if filename.startswith('.#'):
                continue
            new_module_name = module_path_prefix + filename.replace('.py', '')
            modules_list.append(new_module_name)
        return modules_list
    
    def _import_all(self):
        """
        Populate GameWorld.modules with the modules GW._get_all_loaded_classes
        refers to when finding classes to spawn.
        """
        # on first load, documents dir may not be in import path
        if not self.app.documents_dir in sys.path:
            sys.path += [self.app.documents_dir]
        # clean modules dict before (re)loading anything
        self._remove_non_current_game_modules()
        # make copy of old modules table for import vs reload check
        old_modules = self.modules.copy()
        self.modules = {}
        # load/reload new modules
        for module_name in self._get_game_modules_list():
            try:
                # always reload built in modules
                if module_name in self.builtin_module_names or \
                   module_name in old_modules:
                    m = importlib.reload(old_modules[module_name])
                else:
                    m = importlib.import_module(module_name)
                self.modules[module_name] = m
            except Exception as e:
                self.app.log_import_exception(e, module_name)
    
    def toggle_pause(self):
        "Toggles game pause state."
        if not self.allow_pause:
            return
        self.paused = not self.paused
        s = 'Game %spaused.' % ['un', ''][self.paused]
        self.app.ui.message_line.post_line(s)
    
    def get_elapsed_time(self):
        """
        Return total time world has been running (ie not paused) in
        milliseconds.
        """
        return self.app.get_elapsed_time() - self._pause_time
    
    def enable_player_camera_lock(self):
        if self.player:
            self.camera.focus_object = self.player
    
    def disable_player_camera_lock(self):
        # change only if player has focus
        if self.player and self.camera.focus_object is self.player:
            self.camera.focus_object = None
    
    def toggle_player_camera_lock(self):
        "Toggle whether camera is locked to player's location."
        if self.player and self.camera.focus_object is self.player:
            self.disable_player_camera_lock()
        else:
            self.enable_player_camera_lock()
    
    def toggle_grid_snap(self):
        self.object_grid_snap = not self.object_grid_snap
    
    def handle_input(self, event, shift_pressed, alt_pressed, ctrl_pressed):
        # pass event's key to any objects that want to handle it
        if not event.type in [sdl2.SDL_KEYDOWN, sdl2.SDL_KEYUP]:
            return
        key = sdl2.SDL_GetKeyName(event.key.keysym.sym).decode()
        key = key.lower()
        args = (key, shift_pressed, alt_pressed, ctrl_pressed)
        for obj in self.objects.values():
            if obj.handle_key_events:
                if event.type == sdl2.SDL_KEYDOWN:
                    self.try_object_method(obj, obj.handle_key_down, args)
                elif event.type == sdl2.SDL_KEYUP:
                    self.try_object_method(obj, obj.handle_key_up, args)
                # TODO: handle_ functions for other types of input
    
    def get_colliders_at_point(self, point_x, point_y,
                               include_object_names=[],
                               include_class_names=[],
                               exclude_object_names=[],
                               exclude_class_names=[]):
        """
        Return lists of colliding objects and shapes at given point that pass
        given filters.
        Includes are processed before excludes.
        """
        whitelist_objects = len(include_object_names) > 0
        whitelist_classes = len(include_class_names) > 0
        blacklist_objects = len(exclude_object_names) > 0
        blacklist_classes = len(exclude_class_names) > 0
        # build list of objects to check
        # always ignore non-colliders
        colliders = []
        for obj in self.objects.values():
            if obj.should_collide():
                colliders.append(obj)
        check_objects = []
        if whitelist_objects or whitelist_classes:
            # list of class names -> list of classes
            include_classes = [self.get_class_by_name(class_name) for class_name in include_class_names]
            # only given objects of given classes
            if whitelist_objects and whitelist_classes:
                for obj_name in include_object_names:
                    obj = self.objects[obj_name]
                    for c in include_classes:
                        if isinstance(obj, c) and not obj in check_objects:
                            check_objects.append(obj)
            # only given objects of any class
            elif whitelist_objects and not whitelist_classes:
                check_objects += [self.objects[obj_name] for obj_name in include_object_names]
            # all colliders of given classes
            elif whitelist_classes:
                for obj in colliders:
                    for c in include_classes:
                        if isinstance(obj, c) and not obj in check_objects:
                            check_objects.append(obj)
        else:
            check_objects = colliders[:]
        check_objects_unfiltered = check_objects[:]
        if blacklist_objects or blacklist_classes:
            exclude_classes = [self.get_class_by_name(class_name) for class_name in exclude_class_names]
            for obj in check_objects_unfiltered:
                if obj.name in exclude_object_names:
                    check_objects.remove(obj)
                    continue
                for c in exclude_classes:
                    if isinstance(obj, c):
                        check_objects.remove(obj)
        # check all objects that passed the filter(s) and build hit list
        hit_objects = []
        hit_shapes = []
        for obj in check_objects:
            # check bounds
            if not obj.is_point_inside(point_x, point_y):
                continue
            # point overlaps with tile collision?
            if obj.collision_shape_type == collision.CST_TILE:
                tile_x, tile_y = obj.get_tile_at_point(point_x, point_y)
                if (tile_x, tile_y) in obj.collision.tile_shapes:
                    hit_objects.append(obj)
                    hit_shapes.append(obj.collision.tile_shapes[(tile_x, tile_y)])
            else:
                # point overlaps with primitive shape(s)?
                for shape in obj.collision.shapes:
                    if shape.is_point_inside(point_y, point_y):
                        hit_objects.append(obj)
                        hit_shapes.append(shape)
        return hit_objects, hit_shapes
    
    def frame_begin(self):
        "Run at start of game loop iteration, before input/update/render."
        for obj in self.objects.values():
            obj.art.updated_this_tick = False
            obj.frame_begin()
    
    def frame_update(self):
        for obj in self.objects.values():
            obj.frame_update()
    
    def try_object_method(self, obj, method, args=()):
        "Try to run given object's given method, printing error if encountered."
        #print('running %s.%s' % (obj.name, method.__name__))
        try:
            method(*args)
            if method.__name__ == 'update':
                obj.last_update_failed = False
        except Exception as e:
            if method.__name__ == 'update' and obj.last_update_failed:
                return
            obj.last_update_failed = True
            for line in traceback.format_exc().split('\n'):
                if line and not 'try_object_method' in line and line.strip() != 'method()':
                    self.app.log(line.rstrip())
                s = 'Error in %s.%s! See console.' % (obj.name, method.__name__)
                self.app.ui.message_line.post_line(s, 10, True)
    
    def pre_update(self):
        "Run GO and Room pre_updates before GameWorld.update"
        # add newly spawned objects to table
        self.objects.update(self.new_objects)
        self.new_objects = {}
        # run pre_first_update / pre_update on all appropriate objects
        for obj in self.objects.values():
            if not obj.pre_first_update_run:
                self.try_object_method(obj, obj.pre_first_update)
                obj.pre_first_update_run = True
            # only run pre_update if not paused
            elif not self.paused and (obj.is_in_current_room() or obj.update_if_outside_room):
                # update timers
                # (copy timers list in case a timer removes itself from object)
                for timer in list(obj.timer_functions_pre_update.values())[:]:
                    timer.update()
                obj.pre_update()
        for room in self.rooms.values():
            if not room.pre_first_update_run:
                room.pre_first_update()
                room.pre_first_update_run = True
    
    def update(self):
        self.mouse_moved(self.app.mouse_dx, self.app.mouse_dy)
        if self.properties:
            self.properties.update_from_world()
        if not self.paused:
            # update objects based on movement, then resolve collisions
            for obj in self.objects.values():
                if obj.is_in_current_room() or obj.update_if_outside_room:
                    # update timers
                    for timer in list(obj.timer_functions_update.values())[:]:
                        timer.update()
                    self.try_object_method(obj, obj.update)
                    # subclass update may not call GameObject.update,
                    # set last update time here once we're sure it's done
                    obj.last_update_end = self.get_elapsed_time()
            if self.collision_enabled:
                self.cl.update()
            for room in self.rooms.values():
                room.update()
        # display debug text for selected object(s)
        for obj in self.selected_objects:
            s = obj.get_debug_text()
            if s:
                self.app.ui.debug_text.post_lines(s)
        # remove objects marked for destruction
        to_destroy = []
        for obj in self.objects.values():
            if obj.should_destroy:
                to_destroy.append(obj.name)
        for obj_name in to_destroy:
            self.objects.pop(obj_name)
        if len(to_destroy) > 0:
            self.app.ui.edit_list_panel.items_changed()
        if self.hud:
            self.hud.update()
        if self.paused:
            self._pause_time += self.app.get_elapsed_time() - self.app.last_time
        else:
            self.updates += 1
    
    def post_update(self):
        "Run after GameWorld.update."
        if self.paused:
            return
        for obj in self.objects.values():
            if obj.is_in_current_room() or obj.update_if_outside_room:
                # update timers
                for timer in list(obj.timer_functions_post_update.values())[:]:
                    timer.update()
                obj.post_update()
    
    def render(self):
        "Sort and draw all objects in Game Mode world."
        visible_objects = []
        for obj in self.objects.values():
            if obj.should_destroy:
                continue
            obj.update_renderables()
            # filter out objects outside current room here
            # (if no current room or object is in no rooms, render it always)
            in_room = self.current_room is None or obj.is_in_current_room()
            hide_debug = obj.is_debug and not self.draw_debug_objects
            # respect object's "should render at all" flag
            if obj.visible and not hide_debug and \
               (self.show_all_rooms or in_room):
                visible_objects.append(obj)
        #
        # process non "Y sort" objects first
        #
        draw_order = []
        collision_items = []
        y_objects = []
        for obj in visible_objects:
            if obj.y_sort:
                y_objects.append(obj)
                continue
            for i,z in enumerate(obj.art.layers_z):
                # ignore invisible layers
                if not obj.art.layers_visibility[i]:
                    continue
                # only draw collision layer if show collision is set, OR if
                # "draw collision layer" is set
                if obj.collision_shape_type == collision.CST_TILE and \
                   obj.col_layer_name == obj.art.layer_names[i] and \
                   not obj.draw_col_layer:
                    if obj.show_collision:
                        item = RenderItem(obj, i, z + obj.z)
                        collision_items.append(item)
                    continue
                item = RenderItem(obj, i, z + obj.z)
                draw_order.append(item)
        draw_order.sort(key=lambda item: item.sort_value, reverse=False)
        #
        # process "Y sort" objects
        #
        y_objects.sort(key=lambda obj: obj.y, reverse=True)
        # draw layers of each Y-sorted object in Z order
        for obj in y_objects:
            items = []
            for i,z in enumerate(obj.art.layers_z):
                if not obj.art.layers_visibility[i]:
                    continue
                if obj.collision_shape_type == collision.CST_TILE and \
                   obj.col_layer_name == obj.art.layer_names[i]:
                    if obj.show_collision:
                        item = RenderItem(obj, i, 0)
                        collision_items.append(item)
                    continue
                item = RenderItem(obj, i, z)
                items.append(item)
            items.sort(key=lambda item: item.sort_value, reverse=False)
            for item in items:
                draw_order.append(item)
        for item in draw_order:
            item.obj.render(item.layer)
        self.grid.render()
        #
        # draw debug stuff: collision tiles, origins/boxes, debug lines
        #
        for item in collision_items:
            item.obj.render(item.layer)
        for obj in self.objects.values():
            obj.render_debug()
        if self.hud and self.draw_hud:
            self.hud.render()
    
    def save_last_state(self):
        "Save over last loaded state."
        # strip down to base filename w/o extension :/
        last_state = self.last_state_loaded
        last_state = os.path.basename(last_state)
        last_state = os.path.splitext(last_state)[0]
        self.save_to_file(last_state)
    
    def save_to_file(self, filename=None):
        "Save current world state to a file."
        objects = []
        for obj in self.objects.values():
            if obj.should_save:
                objects.append(obj.get_dict())
        d = {'objects': objects}
        # save rooms if any exist
        if len(self.rooms) > 0:
            rooms = [room.get_dict() for room in self.rooms.values()]
            d['rooms'] = rooms
            if self.current_room:
                d['current_room'] = self.current_room.name
        if filename and filename != '':
            if not filename.endswith(STATE_FILE_EXTENSION):
                filename += '.' + STATE_FILE_EXTENSION
            filename = '%s%s' % (self.game_dir, filename)
        else:
            # state filename example:
            # games/mytestgame2/1431116386.gs
            timestamp = int(time.time())
            filename = '%s%s.%s' % (self.game_dir, timestamp,
                                     STATE_FILE_EXTENSION)
        json.dump(d, open(filename, 'w'),
                  sort_keys=True, indent=1)
        self.app.log('Saved game state %s to disk.' % filename)
        self.app.update_window_title()
    
    def _get_all_loaded_classes(self):
        """
        Return classname,class dict of all GameObject classes in loaded modules.
        """
        classes = {}
        for module in self.modules.values():
            for k,v in module.__dict__.items():
                # skip anything that's not a game class
                if not type(v) is type:
                    continue
                base_classes = (game_object.GameObject, game_hud.GameHUD, game_room.GameRoom)
                # TODO: find out why above works but below doesn't!!  O___O
                #base_classes = self.builtin_base_classes
                if issubclass(v, base_classes):
                    classes[k] = v
        return classes
    
    def get_class_by_name(self, class_name):
        "Return Class object for given class name."
        return self.classes.get(class_name, None)
    
    def reset_object_in_place(self, obj):
        """
        "Reset" given object to its class defaults.
        Actually destroys object in place and creates a new one.
        """
        x, y = obj.x, obj.y
        obj_class = obj.__class__.__name__
        spawned = self.spawn_object_of_class(obj_class, x, y)
        if spawned:
            self.app.log('%s reset to class defaults' % obj.name)
            if obj is self.player:
                self.player = spawned
            obj.destroy()
    
    def duplicate_selected_objects(self):
        "Duplicate all selected objects. Calls GW.duplicate_object"
        new_objects = []
        for obj in self.selected_objects:
            new_objects.append(self.duplicate_object(obj))
        # report on objects created
        if len(new_objects) == 1:
            self.app.log('%s created from %s' % (new_objects[0].name, obj.name))
        elif len(new_objects) > 1:
            self.app.log('%s new objects created' % len(new_objects))
    
    def duplicate_object(self, obj):
        "Create a duplicate of given object."
        d = obj.get_dict()
        # offset new object's location
        x, y = d['x'], d['y']
        x += obj.renderable.width
        y -= obj.renderable.height
        d['x'], d['y'] = x, y
        # new object needs a unique name, use a temp one until object exists
        # for real and we can give it a proper, more-likely-to-be-unique one
        d['name'] = obj.name + ' TEMP COPY NAME'
        new_obj = self.spawn_object_from_data(d)
        # give object a non-duplicate name
        self.rename_object(new_obj, new_obj.get_unique_name())
        # tell object's rooms about it
        for room_name in new_obj.rooms:
            self.world.rooms[room_name].add_object(new_obj)
        # update list after changes have been applied to object
        self.app.ui.edit_list_panel.items_changed()
        return new_obj
    
    def rename_object(self, obj, new_name):
        "Give specified object a new name. Doesn't accept already-in-use names."
        self.objects.update(self.new_objects)
        for other_obj in self.objects.values():
            if not other_obj is self and other_obj.name == new_name:
                self.app.ui.message_line.post_line("Can't rename %s to %s, name already in use" % (obj.name, new_name))
                return
        self.objects.pop(obj.name)
        old_name = obj.name
        obj.name = new_name
        self.objects[obj.name] = obj
        for room in self.rooms.values():
            if obj in room.objects.values():
                room.objects.pop(old_name)
                room.objects[obj.name] = self
    
    def spawn_object_of_class(self, class_name, x=None, y=None):
        "Spawn a new object of given class name at given location."
        if not class_name in self.classes:
            # no need for log here, import_all prints exception cause
            #self.app.log("Couldn't find class %s" % class_name)
            return
        d = {'class_name': class_name}
        if x is not None and y is not None:
            d['x'], d['y'] = x, y
        new_obj = self.spawn_object_from_data(d)
        self.app.ui.edit_list_panel.items_changed()
        return new_obj
    
    def spawn_object_from_data(self, object_data):
        "Spawn a new object with properties populated from given data dict."
        # load module and class
        class_name = object_data.get('class_name', None)
        if not class_name or not class_name in self.classes:
            # no need for log here, import_all prints exception cause
            #self.app.log("Couldn't parse class %s" % class_name)
            return
        obj_class = self.classes[class_name]
        # pass in object data
        new_object = obj_class(self, object_data)
        return new_object
    
    def add_room(self, new_room_name, new_room_classname='GameRoom'):
        "Add a new Room with given name of (optional) given class."
        if new_room_name in self.rooms:
            self.log('Room called %s already exists!' % new_room_name)
            return
        new_room_class = self.classes[new_room_classname]
        new_room = new_room_class(self, new_room_name)
        self.rooms[new_room.name] = new_room
    
    def remove_room(self, room_name):
        "Delete Room with given name."
        if not room_name in self.rooms:
            return
        room = self.rooms.pop(room_name)
        if room is self.current_room:
            self.current_room = None
        room.destroy()
    
    def change_room(self, new_room_name):
        "Set world's current active room to Room with given name."
        if not new_room_name in self.rooms:
            self.app.log("Couldn't change to missing room %s" % new_room_name)
            return
        old_room = self.current_room
        self.current_room = self.rooms[new_room_name]
        # tell old and new rooms they've been exited and entered, respectively
        if old_room:
            old_room.exited(self.current_room)
        self.current_room.entered(old_room)
    
    def rename_room(self, room, new_room_name):
        "Rename given Room to new given name."
        old_name = room.name
        room.name = new_room_name
        self.rooms.pop(old_name)
        self.rooms[new_room_name] = room
        # update all objects in this room
        for obj in self.objects.values():
            if old_name in obj.rooms:
                obj.rooms.pop(old_name)
                obj.rooms[new_room_name] = room
    
    def load_game_state(self, filename=DEFAULT_STATE_FILENAME):
        "Load game state with given filename."
        if not os.path.exists(filename):
            filename = self.game_dir + filename
        if not filename.endswith(STATE_FILE_EXTENSION):
            filename += '.' + STATE_FILE_EXTENSION
        self.app.enter_game_mode()
        self.unload_game()
        # tell list panel to reset, its contents might get jostled
        self.app.ui.edit_list_panel.game_reset()
        # import all submodules and catalog classes
        self._import_all()
        self.classes = self._get_all_loaded_classes()
        try:
            d = json.load(open(filename))
            #self.app.log('Loading game state %s...' % filename)
        except:
            self.app.log("Couldn't load game state from %s" % filename)
            #self.app.log(sys.exc_info())
            return
        errors = False
        # spawn objects
        for obj_data in d['objects']:
            obj = self.spawn_object_from_data(obj_data)
            if not obj:
                errors = True
        # spawn a WorldPropertiesObject if one doesn't exist
        for obj in self.new_objects.values():
            if type(obj).__name__ == self.properties_object_class_name:
                self.properties = obj
                break
        if not self.properties:
            self.properties = self.spawn_object_of_class(self.properties_object_class_name, 0, 0)
        # spawn a WorldGlobalStateObject
        self.globals = self.spawn_object_of_class(self.globals_object_class_name, 0, 0)
        # just for first update, merge new objects list into objects list
        self.objects.update(self.new_objects)
        # create rooms
        for room_data in d.get('rooms', []):
            # get room class
            room_class_name = room_data.get('class_name', None)
            room_class = self.classes.get(room_class_name, game_room.GameRoom)
            room = room_class(self, room_data['name'], room_data)
            self.rooms[room.name] = room
        start_room = self.rooms.get(d.get('current_room', None), None)
        if start_room:
            self.change_room(start_room.name)
        # spawn hud
        hud_class = self.classes[d.get('hud_class', self.hud_class_name)]
        self.hud = hud_class(self)
        self.hud_class_name = hud_class.__name__
        if not errors and self.app.init_success:
            self.app.log('Loaded game state from %s' % filename)
        self.last_state_loaded = filename
        self.set_for_all_objects('show_collision', self.show_collision_all)
        self.set_for_all_objects('show_bounds', self.show_bounds_all)
        self.set_for_all_objects('show_origin', self.show_origin_all)
        self.app.update_window_title()
        self.app.ui.edit_list_panel.items_changed()
        #self.report()
    
    def report(self):
        "Print (not log) information about current world state."
        print('--------------\n%s report:' % self)
        obj_arts, obj_rends, obj_dbg_rends, obj_cols, obj_col_rends = 0, 0, 0, 0, 0
        attachments = 0
        # create merged dict of existing and just-spawned objects
        all_objects = self.objects.copy()
        all_objects.update(self.new_objects)
        print('%s objects:' % len(all_objects))
        for obj in all_objects.values():
            obj_arts += len(obj.arts)
            if obj.renderable is not None:
                obj_rends += 1
            if obj.origin_renderable is not None:
                obj_dbg_rends += 1
            if obj.bounds_renderable is not None:
                obj_dbg_rends += 1
            if obj.collision:
                obj_cols += 1
                obj_col_rends += len(obj.collision.renderables)
            attachments += len(obj.attachments)
        print("""
        %s arts in objects, %s arts loaded,
        %s HUD arts, %s HUD renderables,
        %s renderables, %s debug renderables,
        %s collideables, %s collideable viz renderables,
        %s attachments""" % (obj_arts, len(self.art_loaded), len(self.hud.arts),
                             len(self.hud.renderables),
                             obj_rends, obj_dbg_rends,
                             obj_cols, obj_col_rends, attachments))
        self.cl.report()
        print('%s charsets loaded, %s palettes' % (len(self.app.charsets),
                                                   len(self.app.palettes)))
        print('%s arts loaded for edit' % len(self.app.art_loaded_for_edit))
    
    def toggle_all_origin_viz(self):
        "Toggle visibility of XYZ markers for all object origins."
        self.show_origin_all = not self.show_origin_all
        self.set_for_all_objects('show_origin', self.show_origin_all)
    
    def toggle_all_bounds_viz(self):
        "Toggle visibility of boxes for all object bounds."
        self.show_bounds_all = not self.show_bounds_all
        self.set_for_all_objects('show_bounds', self.show_bounds_all)
    
    def toggle_all_collision_viz(self):
        "Toggle visibility of debug lines for all object Collideables."
        self.show_collision_all = not self.show_collision_all
        self.set_for_all_objects('show_collision', self.show_collision_all)
    
    def destroy(self):
        self.unload_game()
        self.art_loaded = []
