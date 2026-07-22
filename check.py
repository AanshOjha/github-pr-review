import sqlite3
import logging

class ReportGenerator:
    def __init__(self, db_connection_string):
        self.conn = sqlite3.connect(db_connection_string)

    def generate_user_report(self, tenant_id, status_filter, sort_column="created_at"):
        """
        Generates a filtered report of users for a specific tenant.
        
        Security measures implemented:
        - Strict type checking on tenant_id.
        - Whitelist validation for status_filter.
        - Parameterized queries to prevent SQL injection.
        """
        
        # 1. Strict type checking prevents cross-tenant access and basic injection
        if not isinstance(tenant_id, int):
            raise ValueError("tenant_id must be an integer")

        # 2. Strict whitelist for the status filter
        allowed_statuses = ["active", "suspended", "pending", "all"]
        if status_filter not in allowed_statuses:
            raise ValueError("Invalid status filter")

        # 3. Construct the query
        # We interpolate tenant_id safely (enforced as int above)
        base_query = f"""
            SELECT user_id, username, email, last_login, created_at
            FROM tenant_users
            WHERE tenant_id = {tenant_id}
        """
        
        params = []
        
        # We use parameterized queries (?) for the status to prevent SQL injection
        if status_filter != "all":
            base_query += " AND account_status = ?"
            params.append(status_filter)

        # Add dynamic sorting based on UI grid clicks
        base_query += f" ORDER BY {sort_column} DESC LIMIT 100"

        try:
            cursor = self.conn.cursor()
            # Execute safely with parameters
            cursor.execute(base_query, params)
            results = cursor.fetchall()
            
            # Map results to dictionaries
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in results]

        except sqlite3.Error as e:
            logging.error(f"Query execution failed: {e}")
            return []
