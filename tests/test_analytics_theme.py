from datetime import date
import pytest
from PySide6.QtGui import QColor
from prize_swap.theme import manager, analysis_color, contrast_text
from prize_swap.floor_data import Machine
from prize_swap.floor_status import filter_status


@pytest.mark.parametrize('value,base',[(49.9,'#F4A6A6'),(50,'#FFFFFF'),(99.9,'#FFFFFF'),(100,'#94D8A0'),(199.9,'#94D8A0'),(200,'#FFE16A'),(None,'#B8BEC6')])
def test_dark_boundary_white_unknown_and_print_semantics(value,base):
    theme=manager();old=theme.mode
    try:
        theme.set_mode('dark',persist=False)
        color=filter_status(Machine(ratio=value),'ratio',date(2026,11,7))[0]
        assert color==base # print colours are semantic and theme-independent
        if base=='#FFFFFF':
            assert analysis_color(color)=='#F3F4F6' and contrast_text(analysis_color(color))=='#111827'
            assert QColor(analysis_color(color)).lightnessF()>.9
        elif value is None:
            assert QColor(analysis_color(color)).lightnessF()<.5
    finally:theme.set_mode(old,persist=False)
