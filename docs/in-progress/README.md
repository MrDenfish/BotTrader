# In Progress Documentation

This directory contains documentation for work that is **actively in progress** but not yet complete.

## What Belongs Here

Documents that describe:
- **Ongoing development efforts** - Features or systems currently being built
- **Data collection phases** - Experiments or optimizations gathering baseline data
- **Pending implementation plans** - Detailed plans waiting on prerequisites or dependencies
- **Active research** - Analysis or investigation that hasn't reached conclusions yet

## When to Move Files Here

Move a document to `in-progress/` when:
- The work described is partially complete but still has active tasks remaining
- You're in the middle of implementing a feature or optimization
- The document is being actively updated as work progresses
- The work depends on other in-progress efforts

## When to Move Files Out

Move documents **OUT** of `in-progress/` when:

### To `active/` directories:
- Work is complete and the system is running in production
- Documentation serves as ongoing reference material
- Content describes current operational procedures or systems

### To `archive/` directories:
- Work is complete but no longer actively maintained
- Historical record of past sessions, deployments, or resolved issues
- Analysis or reports that are finalized and won't be updated

### To `planning/`:
- Work has been deferred or postponed
- Prerequisites haven't been met yet
- Future work waiting on dependencies

## Current In-Progress Work

### STRATEGY_OPTIMIZATION_DEPLOYMENT_GUIDE.md
**Status:** Data collection phase
**Description:** Guide for deploying and monitoring the ROC momentum strategy optimization
**Next Steps:** Collect baseline performance data, analyze results, iterate on parameters

### BOT_OPTIMIZATION_TIMELINE_ANALYSIS.md
**Status:** Pending (depends on STRATEGY_OPTIMIZATION)
**Description:** Timeline analysis for bot-wide optimization efforts
**Next Steps:** Wait for strategy optimization data before proceeding

### TRIGGER_PRESERVATION_IMPROVEMENT_PLAN.md
**Status:** Planning/research phase
**Description:** Plan to improve trigger metadata preservation across system restarts
**Next Steps:** Evaluate approaches, test implementations

## Directory Lifecycle

```
planning/ → in-progress/ → active/ → archive/
    ↓           ↓              ↓          ↓
  Future    Currently      Production  Historical
   Work      Building       Running     Record
```

## Tips for Maintaining This Directory

1. **Keep it current** - Move completed work out promptly
2. **Update regularly** - Keep status notes current in document headers
3. **Link dependencies** - Reference related in-progress work
4. **Add next steps** - Document what's needed to complete the work
5. **Don't let it grow** - More than 5-7 files means work should be completed or archived
