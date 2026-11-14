"""Calendario gare, scheda gara e fatture del Pit Wall.

Le gare sono salvate in dati_gare.json accanto a main.py, così restano
disponibili dopo ogni riavvio. Aprendo una gara si entra nel muretto
con il link Live Timing collegato alla gara.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import pro_tools

DATA_FILE = Path(__file__).with_name("dati_gare.json")
VISTA = "vista_gara"          # "home" oppure id della gara aperta
CARICATA = "_gara_caricata"   # ultima gara il cui link è stato caricato nel muretto


# --------------------------------------------------------------------------- #
# Archivio
# --------------------------------------------------------------------------- #