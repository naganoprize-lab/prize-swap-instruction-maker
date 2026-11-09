import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        from prize_swap.smoke import run

        sys.exit(run(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--floor-visual-test":
        from prize_swap.floor_visual_check import run

        sys.exit(run(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--workspace-visual-test":
        from prize_swap.workspace_visual_check import run

        sys.exit(run(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--analytics-visual-test":
        from prize_swap.analytics_visual_check import run

        sys.exit(run(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--instruction-visual-test":
        from prize_swap.instruction_visual_check import run

        sys.exit(run(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--workflow-visual-test":
        from prize_swap.workflow_visual_check import run

        sys.exit(run(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--filter-visual-test":
        from prize_swap.filter_visual_check import run

        sys.exit(run(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--daily-visual-test":
        from prize_swap.daily_visual_check import run

        sys.exit(run(sys.argv[2]))
    from prize_swap.ui import main

    main()
