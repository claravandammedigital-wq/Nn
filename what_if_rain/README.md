# What if rain disappeared for 5 minutes?

Vidéo verticale (1080×1920, 30 i/s, H.264) générée entièrement par script :
Blender (module `bpy`, moteur EEVEE) pour la 3D low-poly, ffmpeg pour l'assemblage et les textes.

## Fichiers
- `what_if_rain.py` : le script unique. Tout ce qui est modifiable (textes, timings, couleurs, qualité, densité de pluie) est dans le bloc `CONFIG` en haut du fichier.
- `out/what_if_rain.mp4` : la vidéo finale.
- `out/preview_steps_1_and_3.jpg` : la prévisualisation du style (étapes 1 et 3).
- `fonts/` : Playfair Display (titre, verdict) et Montserrat (encart, compte à rebours), sous licence SIL OFL (fichiers `OFL-*.txt`).

## Installation
```bash
python3 -m venv venv
venv/bin/pip install bpy==5.2.4 numpy      # bpy 5.2 = Python 3.13
sudo apt-get install ffmpeg libegl1 libgl1-mesa-dri   # libs EGL pour le rendu EEVEE sans écran
```

## Utilisation
```bash
venv/bin/python what_if_rain.py preview            # images fixes des étapes 1 et 3 -> out/preview/
venv/bin/python what_if_rain.py preview 45 222     # images fixes de frames précises
venv/bin/python what_if_rain.py render             # toutes les frames -> out/frames/
venv/bin/python what_if_rain.py render 0 570 2     # une frame sur deux (lancer 0 et 1 en parallèle)
venv/bin/python what_if_rain.py compose            # frames + textes -> out/what_if_rain.mp4
RAIN_SCALE=50 venv/bin/python what_if_rain.py preview   # brouillon rapide à 50 % de la résolution
```
Le rendu reprend là où il s'est arrêté : les frames déjà présentes sont sautées. Après une
modification de la 3D, supprimez `out/frames/` avant de relancer. Une modification des textes
demande seulement `compose`.

## Déroulé (secondes, réglable dans CONFIG)
| Temps | Étape |
|---|---|
| 0 – 3,5 | Forte pluie, plan large |
| 3,5 – 6,2 | Les gouttes se figent, puis s'évaporent en particules |
| 6,2 – 11,2 | Flaque miroir, puis passants qui baissent leur parapluie et lèvent la tête ; encart « 0 mm » |
| 11,2 – 13,8 | Compte à rebours 5:00 → 0:00 en accéléré |
| 14,0 | La pluie revient d'un coup, les parapluies se rouvrent |
| 15,7 – 19 | Plan final + verdict |

Performances : sur CPU (rendu logiciel Mesa llvmpipe), une frame 1080×1920 prend environ 13 s.
Les ombres (`"shadows"`) et la sonde de réflexion planaire (`"planar_reflections"`) sont
désactivées par défaut, car elles multiplient ce temps par 6 ou plus. Sur une machine avec GPU,
vous pouvez les réactiver.
