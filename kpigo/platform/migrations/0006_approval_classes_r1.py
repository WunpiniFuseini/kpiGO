# Adds the target_publish and manual_input maker-checker classes (R1).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0005_approvalpolicy"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="approvalpolicy",
            name="approval_policy_class_valid",
        ),
        migrations.AddConstraint(
            model_name="approvalpolicy",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "approval_class__in",
                        [
                            "metric_change",
                            "hierarchy_change",
                            "calendar_change",
                            "period_close",
                            "config_change",
                            "access_change",
                            "target_publish",
                            "manual_input",
                        ],
                    )
                ),
                name="approval_policy_class_valid",
            ),
        ),
    ]
