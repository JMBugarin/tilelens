import multiprocessing
import sys

from tilelens.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main(sys.argv[1:] or None))
