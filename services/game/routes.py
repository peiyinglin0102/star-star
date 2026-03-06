from flask import Blueprint, render_template, abort
bp = Blueprint("game", __name__, url_prefix="/game")

@bp.route("/<name>")
def game_page(name):
    file_map = {
        "hamster": "hamster.html",
        "click": "click-game.html",
        "shooting": "shooting_game.html",
        "memory": "memory-game.html",
        "car": "car_go.html",
        "pig": "pig_jump.html",
        "dino": "dinosaur_game.html",
        "emotion": "emotion.html",
        "stack": "stack.html",
        "stick": "stick-hero.html",
    }
    filename = file_map.get(name)
    if not filename: abort(404)
    return render_template(f"GAME/{filename}")
