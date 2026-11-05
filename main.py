import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        from prize_swap.smoke import run
        sys.exit(run(sys.argv[2]))
    from prize_swap.ui import main
    main()
