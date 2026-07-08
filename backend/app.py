from backend.study_network import study_market, print_report

if __name__ == "__main__":
    results = study_market(auto_paper=True)
    print_report(results)
