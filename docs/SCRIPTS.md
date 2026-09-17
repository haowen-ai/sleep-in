# Script authoring

A single `.py` file can define synchronous `main(params)` or run top-level code. Function mode loads the module once under a non-`__main__` name, then calls the function once; a usual `if __name__ == '__main__'` guard is therefore not run in function mode. Do not call `main()` unconditionally at module scope. Static checks use AST and never import the script.

A ZIP project executes `main.py` once as a normal script. It may use imports from its own directory. One enclosing project folder is accepted. Put function invocation inside your ZIP's own entrypoint if necessary. Use UTF-8 source. `.py` files are limited to 1 MiB; ZIP uploads to 20 MiB, expanded files to 100 MiB and 2,000 entries. Absolute/traversal paths, symlinks and secret files are rejected. `.env.example` may contain placeholder documentation; real secrets belong in variables.

Available environment variables:

| Name | Meaning |
| --- | --- |
| TASK_PARAMS_FILE | Path to a UTF-8 JSON object containing string parameters and a recipients array |
| TASK_OUTPUT_DIR | Directory for downloadable output files |
| TASK_RUN_ID | Stable execution ID for your own idempotency logic |

The optional recipients array is input only; the platform does not send mail. User parameters cannot override it. Administrator-defined instance variables are supplied as environment variables; a script-scoped variable takes precedence. Required variable names may be listed in the manifest's `required_variables` array.

```json
{
  "parameters": [
    {"key": "name", "help": "Who should we greet?", "default": "world", "required": false}
  ],
  "required_variables": []
}
```

A `requirements.txt` uses exact pins such as `httpx==0.28.1`. Publication creates an environment and saves the resolved freeze result. Plain standard-library scripts reuse the base interpreter. Installation output is visible to administrators. Failed publication never becomes a selectable version. Publication does not upgrade existing tasks.

Logs are UTF-8, with a combined 20 MiB cap. Files under TASK_OUTPUT_DIR are offered for download, limited to 100 files and 100 MiB combined; symlinks and outside paths are excluded. Output collection errors are distinct from the Python exit status. Do not print secrets, assume a specific deployment path, or write business output into your source directory.

Scheduling uses IANA timezones. Missing days (for example February 31) and nonexistent DST wall times are skipped. Repeated DST wall times run only the first occurrence. Five-field cron may restrict day-of-month or day-of-week, not both. Intervals use elapsed minutes from the enable-time anchor.
