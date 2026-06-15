import sys

with open("mobile_core/commenter.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_try = False
for i, line in enumerate(lines):
    if "img_before_click = self.driver.screenshot()" in line and "logger.info" in lines[i+1]:
        new_lines.append("        try:\n")
        new_lines.append("            " + line.lstrip())
        in_try = True
    elif in_try:
        if line.strip() == "finally:":
            new_lines.append("        finally:\n")
            in_try = False
        else:
            if line.strip() == "":
                new_lines.append("\n")
            else:
                new_lines.append("    " + line)
    else:
        # Before try block or after finally block
        # We already handled 'finally:' above
        new_lines.append(line)

with open("mobile_core/commenter.py", "w") as f:
    f.writelines(new_lines)
