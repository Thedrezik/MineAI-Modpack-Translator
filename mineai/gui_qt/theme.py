"""Dark/Light Modern Dashboard QSS for the Qt interface."""

DARK_QSS = r"""
QWidget {
    color: #E2E8F0;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog, QWidget#AppRoot, QWidget#DashboardBody, QWidget#SidebarHost, QWidget#SidebarContent, QWidget#SidebarViewport { background-color: #12131C; border: none; }
QLabel, QCheckBox, QRadioButton { background-color: transparent; border: none; }
QScrollArea#Sidebar { background-color: #12131C; border: none; }
QFrame#SidebarActions { background-color: #1E1F2E; border: 1px solid #2B2C3D; border-radius: 10px; }
QToolTip {
    background-color: #1E1F2E;
    color: #E2E8F0;
    border: 1px solid #3A3C51;
    border-radius: 7px;
    padding: 7px 10px;
}
QLabel#AppTitle { color: #F8FAFC; font-size: 18px; font-weight: 700; }
QLabel#VersionLabel { color: #94A3B8; font-size: 10px; }
QLabel#SectionTitle { color: #CBC8F8; font-size: 11px; font-weight: 750; letter-spacing: 0.3px; }
QLabel#SectionSubtitle { color: #94A3B8; font-size: 10px; }
QLabel#FieldLabel { color: #B8C1D1; font-size: 11px; font-weight: 600; }
QLabel#MutedLabel { color: #94A3B8; }
QLabel#StrongLabel { color: #F8FAFC; font-weight: 650; }
QLabel#ReadyText { color: #2DD4BF; font-size: 11px; font-weight: 650; }
QLabel#WarningText { color: #F59E0B; }
QLabel#DangerText { color: #EF4444; }
QFrame#Header { background-color: #151622; border: none; border-bottom: 1px solid #2B2C3D; }
QFrame#Sidebar { background-color: transparent; border: none; }
QFrame#Card { background-color: #1E1F2E; border: 1px solid #2B2C3D; border-radius: 10px; }
QFrame#Card:hover { border-color: #35374B; }
QFrame#InnerCard { background-color: #181925; border: 1px solid #292B3D; border-radius: 9px; }
QFrame#ReadyBox { background-color: #15302C; border: 1px solid #235D55; border-radius: 8px; }
QFrame#GlobalReady { background-color: #163028; border: 1px solid #285B4D; border-radius: 8px; }
QFrame#GlobalWarning { background-color: #352A1A; border: 1px solid #6C5122; border-radius: 8px; }
QFrame#Footer { background-color: #151622; border: none; border-top: 1px solid #2B2C3D; }
QLineEdit, QComboBox, QSpinBox {
    min-height: 32px; background-color: #181925; color: #E2E8F0;
    border: 1px solid #34364A; border-radius: 8px; padding: 0 10px;
    selection-background-color: #6B46C1; selection-color: #FFFFFF;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #4A4D64; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #8B6BE5; }
QSpinBox { padding-right: 34px; }
QSpinBox::up-button, QSpinBox::down-button { width: 30px; background-color: #202231; border-left: 1px solid #393B50; }
QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; border-top-right-radius: 7px; border-bottom: 1px solid #393B50; }
QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; border-bottom-right-radius: 7px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background-color: #2B2D40; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { background-color: #151620; color: #646A7D; border-color: #282A38; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down { width: 30px; border: none; background: transparent; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView {
    background-color: #1E1F2E; color: #E2E8F0; border: 1px solid #34364A;
    border-radius: 8px; padding: 5px; outline: none; selection-background-color: #6B46C1;
}
QComboBox QAbstractItemView::item { min-height: 28px; padding: 4px 9px; }
QCheckBox, QRadioButton { color: #DCE3EE; spacing: 8px; }
QCheckBox::indicator { width: 17px; height: 17px; background-color: #181925; border: 1px solid #4B4E65; border-radius: 5px; }
QCheckBox::indicator:hover { border-color: #8B6BE5; }
QCheckBox::indicator:checked { background-color: #6B46C1; border-color: #8B6BE5; }
QRadioButton::indicator { width: 16px; height: 16px; background-color: #181925; border: 1px solid #52556B; border-radius: 8px; }
QRadioButton::indicator:checked { background-color: #6B46C1; border: 4px solid #181925; }
QPushButton {
    min-height: 32px; background-color: #292B3D; color: #E2E8F0;
    border: 1px solid #393B50; border-radius: 8px; padding: 0 13px; font-weight: 600;
}
QPushButton:hover { background-color: #33354A; border-color: #4B4E66; }
QPushButton:pressed { background-color: #242536; }
QPushButton:disabled { background-color: #1A1B27; color: #5C6273; border-color: #262735; }
QPushButton#HeaderButton { background-color: #202231; min-width: 104px; }
QPushButton#HeaderButton:hover { background-color: #2B2D40; border-color: #555872; }
QToolButton#HeaderLanguageToggle {
    background-color: #202231; color: #E2E8F0; border: 1px solid #393B50;
    border-radius: 8px; padding: 0; font-weight: 700;
}
QToolButton#HeaderLanguageToggle:hover { background-color: #2B2D40; border-color: #8B6BE5; color: #FFFFFF; }
QToolButton#ThemeToggle {
    background-color: #202231; color: #E2E8F0; border: 1px solid #393B50;
    border-radius: 8px; padding: 0; font-family: "Segoe UI Symbol", "Segoe UI", sans-serif; font-size: 18px; font-weight: 700;
}
QToolButton#ThemeToggle:hover { background-color: #2B2D40; border-color: #8B6BE5; color: #FFFFFF; }
QToolButton#FolderButton { background-color: #292B3D; color: #E2E8F0; border: 1px solid #393B50; border-radius: 8px; padding: 0; }
QToolButton#FolderButton:hover { background-color: #33354A; border-color: #4B4E66; }
QToolButton#LogToolButton { background-color: #202231; color: #E2E8F0; border: 1px solid #393B50; border-radius: 8px; padding: 0; }
QToolButton#LogToolButton:hover { background-color: #2B2D40; border-color: #8B6BE5; }
QToolButton#ThemeToggle:pressed { background-color: #242536; }
QPushButton#PrimaryButton { min-height: 42px; background-color: #6B46C1; color: #FFFFFF; border: 1px solid #805CD4; font-size: 13px; font-weight: 700; }
QPushButton#PrimaryButton:hover { background-color: #7953D1; border-color: #9677E0; }
QPushButton#PrimaryButton:pressed { background-color: #5D3AAE; }
QPushButton#DangerButton { background-color: #351E27; color: #F87171; border-color: #672B39; }
QPushButton#DangerButton:hover { background-color: #49232D; border-color: #EF4444; }
QPushButton#WarningButton { background-color: #352E20; color: #FBBF24; border-color: #66542B; }
QPushButton#WarningButton:hover { background-color: #473A22; border-color: #F59E0B; }
QPushButton#SegmentButton { min-height: 38px; background-color: #1A1B28; border-color: #34364A; }
QPushButton#SegmentButton:checked { background-color: #392965; border-color: #7655D0; color: #FFFFFF; }
QFrame#SidebarActions QPushButton,
QFrame#SidebarActions QPushButton#PrimaryButton,
QFrame#SidebarActions QPushButton#WarningButton,
QFrame#SidebarActions QPushButton#DangerButton { min-height: 38px; max-height: 38px; }
QToolButton#HelpMarker { color: #A78BFA; background: #242236; border: 1px solid #51486F; border-radius: 10px; font-weight: 800; padding: 0; }
QToolButton#HelpMarker:hover { color: #FFFFFF; background: #6B46C1; border-color: #8B6BE5; }
QFrame#KpiBlue { background-color: #18263B; border: 1px solid #285089; border-radius: 10px; }
QFrame#KpiGreen { background-color: #17322B; border: 1px solid #285A4E; border-radius: 10px; }
QFrame#KpiAmber { background-color: #332619; border: 1px solid #79501F; border-radius: 10px; }
QFrame#KpiViolet { background-color: #27203C; border: 1px solid #5B4385; border-radius: 10px; }
QLabel#KpiCaption { color: #AEB9CC; font-size: 10px; font-weight: 700; }
QLabel#KpiValue { color: #F8FAFC; font-size: 22px; font-weight: 750; }
QLabel#TaskPercent { color: #F8FAFC; font-size: 19px; font-weight: 750; }
QLabel#KpiMeta { color: #94A3B8; font-size: 9px; }
QProgressBar { min-height: 12px; max-height: 12px; background-color: #282A3A; border: none; border-radius: 6px; text-align: center; color: transparent; }
QProgressBar::chunk { background-color: #2DD4BF; border-radius: 6px; }
QProgressBar#VioletProgress::chunk { background-color: #7C5CE0; }
QTabWidget::pane { border: 1px solid #2B2C3D; border-radius: 8px; background: #1E1F2E; }
QTabBar::tab { background: #181925; color: #94A3B8; border: 1px solid #2B2C3D; padding: 9px 14px; margin-right: 3px; border-top-left-radius: 7px; border-top-right-radius: 7px; }
QTabBar::tab:selected { background: #2A2142; color: #E2E8F0; border-color: #6B46C1; }
QSlider::groove:horizontal { height: 6px; background: #2A2C3C; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #6B46C1; border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; margin: -5px 0; background: #C7B5F6; border: 2px solid #6B46C1; border-radius: 8px; }
QPlainTextEdit#LogView, QPlainTextEdit { background-color: #151620; color: #CBD5E1; border: 1px solid #292B3D; border-radius: 8px; padding: 10px; font-family: "Cascadia Mono", "Consolas", monospace; font-size: 11px; selection-background-color: #6B46C1; }
QTreeWidget#TranslationSelectionTree { background-color: #151620; color: #DCE3EE; border: 1px solid #292B3D; border-radius: 8px; outline: none; alternate-background-color: #191A26; selection-background-color: #243A56; }
QTreeWidget#TranslationSelectionTree::item { min-height: 30px; padding: 4px 7px; border: 1px solid transparent; }
QTreeWidget#TranslationSelectionTree::item:hover { background-color: #202B3A; border-color: #3C526B; color: #F8FAFC; }
QTreeWidget#TranslationSelectionTree::indicator { width: 17px; height: 17px; background-color: #181925; border: 1px solid #596174; border-radius: 5px; }
QTreeWidget#TranslationSelectionTree::indicator:hover { border-color: #7CA5D1; }
QTreeWidget#TranslationSelectionTree::indicator:checked { background-color: #20C7B7; border-color: #5EEAD4; }
QTreeWidget#TranslationSelectionTree QHeaderView::section { background-color: #202231; color: #B8C1D1; border: none; border-bottom: 1px solid #34364A; padding: 8px; font-weight: 650; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background-color: transparent; }
QScrollBar:vertical { width: 9px; background: transparent; margin: 4px 2px 4px 2px; }
QScrollBar::handle:vertical { min-height: 38px; background: #3A3C50; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #55586F; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 9px; background: transparent; margin: 2px 4px; }
QScrollBar::handle:horizontal { min-width: 38px; background: #3A3C50; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QSplitter::handle { background: #2B2C3D; }
"""

LIGHT_OVERRIDES = r"""
QWidget { color: #2D3442; }
QMainWindow, QDialog { background-color: #E4E8EE; }
QWidget#AppRoot, QWidget#SidebarHost, QWidget#SidebarContent, QWidget#SidebarViewport, QWidget#DashboardBody { background-color: #E4E8EE; }
QScrollArea#Sidebar { background-color: #E4E8EE; }
QFrame#SidebarActions { background-color: #EFF2F6; border-color: #C9D0DA; }
QToolTip { background-color: #F8F9FC; color: #283142; border-color: #C8CED8; }
QLabel#AppTitle, QLabel#StrongLabel, QLabel#KpiValue { color: #171925; }
QLabel#VersionLabel, QLabel#SectionSubtitle, QLabel#MutedLabel, QLabel#KpiMeta { color: #697386; }
QLabel#SectionTitle { color: #4C3F78; }
QLabel#FieldLabel, QLabel#KpiCaption { color: #596579; }
QFrame#Header, QFrame#Footer { background-color: #E9EDF3; border-color: #C9D0DA; }
QFrame#Card { background-color: #EFF2F6; border-color: #C9D0DA; }
QFrame#Card:hover { border-color: #C4CAD8; }
QFrame#InnerCard { background-color: #E9EDF3; border-color: #CDD3DD; }
QFrame#ReadyBox { background-color: #EAFBF7; border-color: #9DE5D7; }
QFrame#GlobalReady { background-color: #EAFBF7; border-color: #9DE5D7; }
QFrame#GlobalWarning { background-color: #FFF8E8; border-color: #EACD8A; }
QLineEdit, QComboBox, QSpinBox { background-color: #E9EDF3; color: #283142; border-color: #BFC7D2; selection-background-color: #6B46C1; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #9CA5B7; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { background-color: #ECEEF3; color: #9299A8; border-color: #D7DBE4; }
QComboBox QAbstractItemView { background-color: #F8F9FC; color: #283142; border-color: #C8CED8; }
QCheckBox, QRadioButton { color: #303544; }
QCheckBox::indicator, QRadioButton::indicator { background-color: #F6F7FA; border-color: #AEB6C6; }
QRadioButton::indicator:checked { background-color: #6B46C1; border-color: #F6F7FA; }
QPushButton { background-color: #ECEEF4; color: #272B37; border-color: #CDD2DD; }
QPushButton:hover { background-color: #E1E4EC; border-color: #B6BDCB; }
QPushButton:pressed { background-color: #D6DAE4; }
QPushButton:disabled { background-color: #EEF0F4; color: #A2A8B5; border-color: #DDE1E8; }
QPushButton#HeaderButton { background-color: #F1F2F6; }
QPushButton#HeaderButton:hover { background-color: #E6E8EF; border-color: #B7BECC; }
QToolButton#HeaderLanguageToggle { background-color: #E5E9F0; color: #272B37; border-color: #BFC7D2; }
QToolButton#HeaderLanguageToggle:hover { background-color: #DCE1E9; border-color: #7655D0; color: #4D2C9B; }
QToolButton#ThemeToggle { background-color: #F1F2F6; color: #4B5568; border-color: #CDD2DD; }
QToolButton#ThemeToggle:hover { background-color: #E6E8EF; border-color: #7655D0; color: #4D2C9B; }
QToolButton#FolderButton { background-color: #EEF0F4; color: #374151; border-color: #CDD2DD; }
QToolButton#FolderButton:hover { background-color: #DCE1E9; border-color: #AEB7C5; }
QToolButton#LogToolButton { background-color: #E5E9F0; color: #374151; border-color: #BFC7D2; }
QToolButton#LogToolButton:hover { background-color: #DCE1E9; border-color: #7655D0; }
QPushButton#PrimaryButton { background-color: #7652D6; color: #FFFFFF; border-color: #6845C5; }
QPushButton#PrimaryButton:hover { background-color: #6845C5; border-color: #5B39B5; }
QPushButton#WarningButton { background-color: #F7EAC7; color: #77520A; border-color: #D8B65B; }
QPushButton#WarningButton:hover { background-color: #F0DDA8; border-color: #C89D34; }
QPushButton#DangerButton { background-color: #F6DFE4; color: #A83249; border-color: #D99AA8; }
QPushButton#DangerButton:hover { background-color: #EFCFD6; border-color: #C97C8E; }
QSpinBox::up-button, QSpinBox::down-button { background-color: #DDE2E9; border-left-color: #BFC7D2; }
QSpinBox::up-button { border-bottom-color: #BFC7D2; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background-color: #D1D7E0; }
QPushButton#SegmentButton { background-color: #F2F3F7; border-color: #CDD2DD; }
QPushButton#SegmentButton:checked { background-color: #EEE9FB; border-color: #7655D0; color: #4D2C9B; }
QToolButton#HelpMarker { color: #6B46C1; background: #F0ECFB; border-color: #C8B9EE; }
QFrame#KpiBlue { background-color: #EEF6FF; border-color: #B7D5F4; }
QFrame#KpiGreen { background-color: #ECFBF7; border-color: #AEE3D8; }
QFrame#KpiAmber { background-color: #FFF7E9; border-color: #E8CF9D; }
QFrame#KpiViolet { background-color: #F3EEFF; border-color: #CEBDF1; }
QProgressBar { background-color: #E0E4EB; }
QTabWidget::pane { border-color: #D7DBE4; background: #F8F9FC; }
QTabBar::tab { background: #EEF0F5; color: #687386; border-color: #D9DDE7; }
QTabBar::tab:selected { background: #EEE9FB; color: #4D2C9B; border-color: #6B46C1; }
QSlider::groove:horizontal { background: #D9DDE7; }
QPlainTextEdit#LogView, QPlainTextEdit { background-color: #E9EDF3; color: #263142; border-color: #C9D0DA; }
QTreeWidget#TranslationSelectionTree { background-color: #F8F9FC; color: #283142; border-color: #C9D0DA; alternate-background-color: #EEF1F5; selection-background-color: #DCEAF8; }
QTreeWidget#TranslationSelectionTree::item:hover { background-color: #E5EEF8; border-color: #9EB8D3; color: #172033; }
QTreeWidget#TranslationSelectionTree::indicator { background-color: #F6F7FA; border-color: #AEB6C6; }
QTreeWidget#TranslationSelectionTree::indicator:hover { border-color: #5484B4; }
QTreeWidget#TranslationSelectionTree::indicator:checked { background-color: #0F9F92; border-color: #087E75; }
QTreeWidget#TranslationSelectionTree QHeaderView::section { background-color: #E5E9F0; color: #596579; border-bottom-color: #BFC7D2; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #B8BFCD; }
QScrollBar::handle:vertical:hover { background: #949DAC; }
"""


def theme_qss(theme: str) -> str:
    """Return the complete stylesheet for a persisted theme name."""
    return DARK_QSS + (LIGHT_OVERRIDES if str(theme).casefold() == "light" else "")


# Backward-compatible alias used by early preview code/tests.
APP_QSS = DARK_QSS
