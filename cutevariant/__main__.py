from PySide2.QtWidgets import *
from PySide2.QtCore import *
import sys
from cutevariant.gui import MainWindow
from cutevariant.gui.wizard.projetwizard import ProjetWizard

def main():
    """The main routine."""

    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    # w = MainWindow()
    # w.show()
    app.exec_()

if __name__ == "__main__":

    main()
