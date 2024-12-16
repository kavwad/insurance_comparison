import streamlit as st
import pandas as pd
import plotly.express as px
from dataclasses import dataclass
from typing import Dict, List
import json
import os

def get_insurer_colors(insurers):
    """Create a consistent color mapping for insurers using Plotly's qualitative colors"""
    colors = px.colors.qualitative.Set2
    return {insurer: colors[i % len(colors)] for i, insurer in enumerate(sorted(insurers))}

@dataclass
class InsurancePlan:
    name: str
    type: str
    insurer: str
    premium: float
    deductibles: Dict
    out_of_pocket_limit: Dict
    referral_needed: bool
    cost_sharing: Dict
    services_covered_before_deductible: List[str]

    def to_dict(self):
        return {
            "name": self.name,
            "type": self.type,
            "insurer": self.insurer,
            "premium": self.premium,
            "deductibles": self.deductibles,
            "out_of_pocket_limit": self.out_of_pocket_limit,
            "referral_needed": self.referral_needed,
            "cost_sharing": self.cost_sharing,
            "services_covered_before_deductible": self.services_covered_before_deductible
        }

# Initialize session state for plans if it doesn't exist
if 'plans' not in st.session_state:
    st.session_state.plans = {}
    # Load plans from JSON if file exists
    if os.path.exists('plans.json'):
        with open('plans.json', 'r') as f:
            plans_data = json.load(f)
            for plan_data in plans_data['plans']:
                plan = InsurancePlan(
                    name=plan_data['plan_name'],
                    type=plan_data['plan_type'],
                    insurer=plan_data['insurer'],
                    premium=plan_data['premium'],
                    deductibles=plan_data['deductibles'],
                    out_of_pocket_limit=plan_data['out_of_pocket_limit'],
                    referral_needed=plan_data['referral_needed'],
                    cost_sharing=plan_data['cost_sharing'],
                    services_covered_before_deductible=plan_data.get('services_covered_before_deductible', [])
                )
                st.session_state.plans[plan.name] = plan

# Define service mapping for cost sharing lookups
service_mapping = {
    "primary_care": "primary_care",
    "specialist": "specialist",
    "urgent_care": "urgent_care",
    "emergency_room": "emergency_room.care",
    "lab_work": "diagnostic_test.lab",
    "generic_drugs": "prescription_drugs.tier_1",
    "specialty_drugs": "prescription_drugs.tier_4",
    "ambulance": "emergency_room.transportation",
    "hospital_stay": "hospital_stay.facility_fee"
}

def calculate_service_costs(plan: InsurancePlan, usage: Dict[str, int]) -> Dict[str, float]:
    """Calculate costs for all services while properly tracking deductible and out-of-pocket maximum"""
    
    # Initialize tracking variables
    accumulated_deductible = 0
    accumulated_total = 0
    service_costs = {}
    out_of_pocket_max = plan.out_of_pocket_limit["individual"]
    deductible = plan.deductibles["overall"]
    services_covered = plan.services_covered_before_deductible
    
    # Define base costs
    base_service_costs = {
        "primary_care": 150,
        "specialist": 250,
        "urgent_care": 200,
        "emergency_room": 1000,
        "lab_work": 300,
        "generic_drugs": 30,
        "specialty_drugs": 600,
        "ambulance": 1200,     
        "hospital_stay": 2500  
    }
    
    # First pass: Calculate raw costs and track deductible
    for service, visits in usage.items():
        if visits == 0:
            service_costs[service] = 0
            continue
            
        base_cost = base_service_costs[service] * visits
        
        # Get cost sharing info
        mapped_service = service_mapping[service]
        service_path = mapped_service.split('.')
        cost_info = plan.cost_sharing
        for path_part in service_path:
            cost_info = cost_info[path_part]
        in_network = cost_info["in_network"]
        
        # Calculate service cost based on type of cost sharing
        if "copay" in in_network:
            # If service has a copay, use it directly
            service_costs[service] = visits * in_network["copay"]
        elif service in ["generic_drugs", "specialty_drugs"]:
            # Handle prescription drugs separately
            if service == "generic_drugs":
                service_costs[service] = visits * in_network["retail_copay"]
            else:
                coinsurance = in_network["retail_coinsurance"] / 100
                retail_max = in_network["retail_max"]
                service_costs[service] = min(base_cost * coinsurance, visits * retail_max)
        else:
            # Handle coinsurance
            if service in services_covered:
                service_costs[service] = base_cost * (in_network["coinsurance"] / 100)
            else:
                # Service is subject to deductible
                remaining_deductible = max(0, deductible - accumulated_deductible)
                if remaining_deductible > 0:
                    # Part or all of the cost goes to deductible
                    deductible_portion = min(base_cost, remaining_deductible)
                    accumulated_deductible += deductible_portion
                    
                    if base_cost > deductible_portion:
                        # Calculate cost sharing for amount above deductible
                        remaining_cost = base_cost - deductible_portion
                        if "copay" in in_network:
                            service_costs[service] = deductible_portion + (visits * in_network["copay"])
                        else:
                            service_costs[service] = deductible_portion + (remaining_cost * (in_network["coinsurance"] / 100))
                    else:
                        service_costs[service] = deductible_portion
                else:
                    # Deductible already met
                    if "copay" in in_network:
                        service_costs[service] = visits * in_network["copay"]
                    else:
                        service_costs[service] = base_cost * (in_network["coinsurance"] / 100)
        
        accumulated_total += service_costs[service]
    
    # Second pass: Adjust for out-of-pocket maximum if needed
    if accumulated_total > out_of_pocket_max:
        # Scale all costs down proportionally to exactly hit the out-of-pocket max
        adjustment_ratio = out_of_pocket_max / accumulated_total
        for service in service_costs:
            service_costs[service] *= adjustment_ratio
            
        # Verify total medical costs exactly equal the out-of-pocket max
        total_medical = sum(service_costs.values())
        assert abs(total_medical - out_of_pocket_max) < 0.01, "Medical costs exceed out-of-pocket maximum"
    
    return service_costs

def calculate_annual_cost(plan: InsurancePlan, usage: Dict[str, int]) -> Dict[str, float]:
    """Calculate total annual cost including premium"""
    # Premium calculation
    annual_premium = plan.premium * 12
    
    # Get costs for all services (this will be capped at out-of-pocket max)
    service_costs = calculate_service_costs(plan, usage)
    total_medical_costs = sum(service_costs.values())
    
    # Verify total cost never exceeds premium + out-of-pocket max
    total_cost = annual_premium + total_medical_costs
    max_possible_cost = annual_premium + plan.out_of_pocket_limit["individual"]
    assert total_cost <= max_possible_cost + 0.01, f"Total cost {total_cost} exceeds maximum possible {max_possible_cost}"
    
    return {
        "annual_premium": annual_premium,
        "medical_costs": total_medical_costs,
        "total_cost": total_cost,
        "service_costs": service_costs
    }

def generate_cost_curve_data(plan: InsurancePlan, max_medical_cost: float = 50000, points: int = 100) -> tuple:
    """Generate data points for cost curve visualization"""
    medical_costs = [i * (max_medical_cost / points) for i in range(points + 1)]
    total_costs = []
    
    for raw_cost in medical_costs:
        # Create a realistic distribution of services that adds up to the target cost
        usage = {
            "primary_care": 0,
            "specialist": 0,
            "urgent_care": 0,
            "emergency_room": 0,
            "lab_work": 0,
            "generic_drugs": 0,
            "specialty_drugs": 0,
            "ambulance": 0,
            "hospital_stay": 0
        }
        
        if raw_cost > 0:
            # For low costs (<$1000), mostly primary care and generic drugs
            if raw_cost <= 1000:
                usage["primary_care"] = (raw_cost * 0.6) / 150  # 60% primary care
                usage["generic_drugs"] = (raw_cost * 0.4) / 30  # 40% generic drugs
            
            # For medium costs ($1000-$5000), add specialists and some urgent care
            elif raw_cost <= 5000:
                usage["primary_care"] = (raw_cost * 0.3) / 150
                usage["specialist"] = (raw_cost * 0.3) / 250
                usage["urgent_care"] = (raw_cost * 0.2) / 200
                usage["generic_drugs"] = (raw_cost * 0.1) / 30
                usage["lab_work"] = (raw_cost * 0.1) / 300
            
            # For high costs ($5000+), include more expensive services
            else:
                usage["hospital_stay"] = (raw_cost * 0.4) / 2500
                usage["emergency_room"] = (raw_cost * 0.2) / 1000
                usage["specialist"] = (raw_cost * 0.15) / 250
                usage["lab_work"] = (raw_cost * 0.15) / 300
                usage["specialty_drugs"] = (raw_cost * 0.1) / 600
        
        # Round all values to nearest whole number since visits can't be fractional
        usage = {k: round(max(0, v)) for k, v in usage.items()}
        
        annual_costs = calculate_annual_cost(plan, usage)
        total_costs.append(annual_costs["total_cost"])

    return medical_costs, total_costs

def main():
    st.set_page_config(
        page_title="Health Insurance Plan Comparison",
        page_icon=":hospital:",
        layout="wide"
    )
    
    st.title("Health Insurance Plan Comparison")

    st.write(
        """
        Use the sidebar to build a usage scenario and compare your chosen plans.
        """
    )
    
    # Sidebar: Scenario Builder
    st.sidebar.header("Usage Scenario")
    usage = {
        "primary_care": st.sidebar.number_input("Primary Care Visits", 0, 50, 0),
        "specialist": st.sidebar.number_input("Specialist Visits", 0, 50, 0),  # Added specialist visits
        "urgent_care": st.sidebar.number_input("Urgent Care Visits", 0, 20, 0),
        "emergency_room": st.sidebar.number_input("ER Visits", 0, 10, 0),
        "lab_work": st.sidebar.number_input("Lab Tests", 0, 50, 0),
        "generic_drugs": st.sidebar.number_input("Generic Drug Prescriptions", 0, 50, 0),
        "specialty_drugs": st.sidebar.number_input("Specialty Drug Prescriptions", 0, 20, 0)
    }
    
    st.sidebar.divider()
    
    emergency_scenario = st.sidebar.checkbox("Add Emergency Scenario", 
        help="Simulates a serious medical event including ambulance, ER visit, hospital stay, and follow-up care")
    
    if emergency_scenario:
        st.sidebar.write("##### Emergency Scenario Details")
        hospital_days = st.sidebar.slider("Hospital Stay (days)", 1, 10, 3)
        
        # Automatically add emergency services to the usage dict
        usage["emergency_room"] += 1  # One ER visit
        usage["ambulance"] = 1  # One ambulance ride
        usage["hospital_stay"] = hospital_days  # Hospital stay
        usage["specialist"] += 2  # Follow-up specialist visits
        usage["lab_work"] += 3  # Additional tests
        usage["generic_drugs"] += 2  # Typical prescriptions
        
        # Show the scenario details
        st.sidebar.write(
            f"""
            This scenario includes:
            - Ambulance transport
            - Emergency room visit
            - {hospital_days} day hospital stay
            - 2 follow-up specialist visits
            - 3 lab tests
            - 2 prescription medications
            """
        )
    
    # Main area tabs
    tab1, tab2 = st.tabs(["Compare Plans", "Manage Plans"])
    
    # Tab 1: Compare Plans
    with tab1:
        if not st.session_state.plans:
            st.info("Please add some insurance plans in the 'Manage Plans' tab or in a `plans.json` file to get started.")
        else:
            # Group plans by insurer
            insurers = {}
            for plan_name, plan in st.session_state.plans.items():
                if plan.insurer not in insurers:
                    insurers[plan.insurer] = []
                insurers[plan.insurer].append(plan)
            
            # Sort plans within each insurer by premium
            for insurer in insurers:
                insurers[insurer].sort(key=lambda x: x.premium)
            
            # Create results list maintaining the sorting
            results = []
            for insurer in sorted(insurers.keys()):
                for plan in insurers[insurer]:
                    # Calculate costs including service breakdown
                    costs = calculate_annual_cost(plan, usage)
                    
                    results.append({
                        "Insurer": plan.insurer,
                        "Plan": plan.name,
                        "Monthly Premium": plan.premium,
                        "Annual Premium": costs["annual_premium"],
                        "Primary Care": costs["service_costs"].get("primary_care", 0),
                        "Specialist": costs["service_costs"].get("specialist", 0),
                        "Urgent Care": costs["service_costs"].get("urgent_care", 0),
                        "Emergency Room": costs["service_costs"].get("emergency_room", 0),
                        "Ambulance": costs["service_costs"].get("ambulance", 0),
                        "Hospital Stay": costs["service_costs"].get("hospital_stay", 0),
                        "Lab Work": costs["service_costs"].get("lab_work", 0),
                        "Generic Drugs": costs["service_costs"].get("generic_drugs", 0),
                        "Specialty Drugs": costs["service_costs"].get("specialty_drugs", 0),
                        "Medical Costs": costs["medical_costs"],
                        "Total Cost": costs["total_cost"]
                    })
            
            results_df = pd.DataFrame(results)
            
            # Format currency columns
            currency_columns = [
                "Monthly Premium", "Annual Premium", "Primary Care", "Specialist",
                "Urgent Care", "Emergency Room", "Ambulance", "Hospital Stay",
                "Lab Work", "Generic Drugs", "Specialty Drugs", "Medical Costs", "Total Cost"
            ]
            for col in currency_columns:
                results_df[col] = results_df[col].apply(lambda x: f"${x:,.2f}")
            
            # Display results table with better formatting
            st.subheader("Cost Comparison")
            st.dataframe(
                results_df,
                column_config={
                    "Insurer": st.column_config.TextColumn("Insurer"),
                    "Plan": st.column_config.TextColumn("Plan"),
                    "Monthly Premium": st.column_config.TextColumn("Monthly Premium"),
                    "Annual Premium": st.column_config.TextColumn("Annual Premium"),
                    "Primary Care": st.column_config.TextColumn("Primary Care"),
                    "Specialist": st.column_config.TextColumn("Specialist"),
                    "Urgent Care": st.column_config.TextColumn("Urgent Care"),
                    "Emergency Room": st.column_config.TextColumn("Emergency Room"),
                    "Ambulance": st.column_config.TextColumn("Ambulance"),
                    "Hospital Stay": st.column_config.TextColumn("Hospital Stay"),
                    "Lab Work": st.column_config.TextColumn("Lab Work"),
                    "Generic Drugs": st.column_config.TextColumn("Generic Drugs"),
                    "Specialty Drugs": st.column_config.TextColumn("Specialty Drugs"),
                    "Medical Costs": st.column_config.TextColumn("Medical Costs"),
                    "Total Cost": st.column_config.TextColumn("Total Cost")
                },
                hide_index=True
            )
            
            # Convert currency strings back to float for plotting
            plot_df = results_df.copy()
            for col in currency_columns:
                plot_df[col] = plot_df[col].str.replace('$', '').str.replace(',', '').astype(float)
            
            # Get consistent colors for insurers
            insurer_colors = get_insurer_colors(insurers.keys())
            
            # Display bar chart with color by insurer
            fig = px.bar(plot_df, 
                        x="Plan", 
                        y="Total Cost",
                        color="Insurer",
                        title="Annual Cost Comparison",
                        color_discrete_map=insurer_colors)
            fig.update_layout(yaxis_title="Total Cost ($)")
            st.plotly_chart(fig)
            
            # Add cost curve visualization
            st.subheader("Cost Sharing Analysis")
            st.write(
                """
                This chart shows how out-of-pocket costs scale with total medical costs for each plan.
                - For low costs (<\\$1000), the model assumes 60% primary care and 40% generic drugs.
                - For medium costs (\\$1000-\\$5000), the model assumes 30% primary care, 30% specialist, 20% urgent care, 10% lab work, 10% generic drugs.
                - For high costs (\\$5000+), the model assumes 40% hospital stay, 20% emergency room, 15% specialist, 15% lab work, 10% specialty drugs.
                
                Use the chart's scaling features to zoom in on your area of interest.
                """
            )
            
            # Generate cost curve data for each plan
            cost_curve_data = []
            
            # Group plans by insurer and sort insurers
            insurers = {}
            for plan_name, plan in st.session_state.plans.items():
                if plan.insurer not in insurers:
                    insurers[plan.insurer] = []
                insurers[plan.insurer].append(plan)
            
            # Sort plans within each insurer by premium
            for insurer in insurers:
                insurers[insurer].sort(key=lambda x: x.premium)
            
            # Generate data in sorted order
            for insurer in sorted(insurers.keys()):
                for plan in insurers[insurer]:
                    medical_costs, out_of_pocket_costs = generate_cost_curve_data(plan)
                    cost_curve_data.extend([{
                        'Medical Costs': med,
                        'Out of Pocket': oop,
                        'Plan': plan.name,
                        'Insurer': plan.insurer
                    } for med, oop in zip(medical_costs, out_of_pocket_costs)])
            
            cost_curve_df = pd.DataFrame(cost_curve_data)
            
            # Create line plot with same colors
            fig_curve = px.line(cost_curve_df,
                              x='Medical Costs',
                              y='Out of Pocket',
                              color='Insurer',
                              line_dash='Plan',
                              title='Total Costs vs Medical Costs',
                              color_discrete_map=insurer_colors)
            
            # Update hover template
            fig_curve.update_traces(
                hovertemplate="%{data.name}<br>Total cost: $%{y:,.2f}<extra></extra>",
            )
            
            fig_curve.update_layout(
                xaxis_title="Total Medical Costs (Before Insurance) ($)",
                yaxis_title="Total Annual Cost (Premium + Out of Pocket) ($)",
                hovermode='x unified',
                height=600,
                legend=dict(orientation="h")
            )
            
            # Format axis labels to show currency
            fig_curve.update_layout(
                xaxis=dict(tickformat="$,.0f"),
                yaxis=dict(tickformat="$,.0f")
            )
            
            st.plotly_chart(fig_curve)
    
    # Tab 2: Manage Plans
    with tab2:
        with st.popover("Add New Plan", icon="➕", use_container_width=True):
            st.subheader("Add New Plan")
            with st.form("new_plan"):
                name = st.text_input("Plan Name")
                premium = st.number_input("Monthly Premium", min_value=0.0, value=0.0)
                deductible = st.number_input("Annual Deductible", min_value=0.0, value=0.0)
                out_of_pocket_max = st.number_input("Out of Pocket Maximum", min_value=0.0, value=0.0)
                plan_type = st.selectbox("Plan Type", ["HMO", "PPO", "EPO"])
                insurer = st.text_input("Insurance Company")
                referral_needed = st.checkbox("Referral Required for Specialists")
                
                st.write("Copays")
                copay_primary = st.number_input("Primary Care Copay", min_value=0.0, value=0.0)
                copay_urgent = st.number_input("Urgent Care Copay", min_value=0.0, value=0.0)
                copay_er = st.number_input("Emergency Room Copay", min_value=0.0, value=0.0)
                
                st.write("Coinsurance (as decimal, e.g., 0.2 for 20%)")
                coinsurance_lab = st.number_input("Lab Work Coinsurance", min_value=0.0, max_value=1.0, value=0.0)
                coinsurance_generic = st.number_input("Generic Drugs Coinsurance", min_value=0.0, max_value=1.0, value=0.0)
                coinsurance_specialty = st.number_input("Specialty Drugs Coinsurance", min_value=0.0, max_value=1.0, value=0.0)
                
                # Add multi-select for pre-deductible services
                services_covered = st.multiselect(
                    "Services Covered Before Deductible",
                    ["Primary Care", "Specialist Visit", "Preventive Care", 
                    "Diagnostic Tests", "Prescription Drugs", "Maternity Care"],
                    default=["Preventive Care"]
                )
                
                st.subheader("Cost Sharing Structure")

                with st.expander("Specialist Care"):
                    specialist_copay = st.number_input("Specialist Visit Copay", min_value=0.0)
                
                with st.expander("Emergency Services"):
                    er_copay = st.number_input("Emergency Room Copay", min_value=0.0)
                    ambulance_copay = st.number_input("Ambulance Copay", min_value=0.0)

                with st.expander("Hospital Services"):
                    hospital_coinsurance = st.number_input("Hospital Stay Coinsurance (%)", min_value=0.0, max_value=100.0)

                with st.expander("Diagnostic Services"):
                    lab_copay = st.number_input("Lab Work Copay", min_value=0.0)
                
                with st.expander("Prescription Drugs"):
                    col1, col2 = st.columns(2)
                    with col1:
                        generic_copay = st.number_input("Generic Drug Copay", min_value=0.0)
                    with col2:
                        specialty_coinsurance = st.number_input("Specialty Drug Coinsurance (%)", min_value=0.0, max_value=100.0)
                        specialty_max = st.number_input("Specialty Drug Maximum", min_value=0.0)
                
                with st.expander("Family Coverage"):
                    family_deductible = st.number_input("Family Deductible", min_value=0.0)
                    family_out_of_pocket = st.number_input("Family Out-of-Pocket Maximum", min_value=0.0)
                
                with st.expander("Out-of-Network Coverage"):
                    has_out_of_network = st.checkbox("Plan includes out-of-network coverage")
                    if has_out_of_network:
                        oon_coinsurance = st.number_input("Out-of-Network Coinsurance (%)", min_value=0.0, max_value=100.0)
                        oon_deductible = st.number_input("Out-of-Network Deductible", min_value=0.0)
                
                if st.form_submit_button("Add Plan"):
                    # Create proper nested structure for cost sharing
                    cost_sharing = {
                        "primary_care": {
                            "in_network": {"copay": copay_primary},
                            "out_of_network": {"coverage": "Not covered"}
                        },
                        "specialist": {
                            "in_network": {"copay": specialist_copay},
                            "out_of_network": {"coverage": "Not covered"}
                        },
                        "urgent_care": {
                            "in_network": {"copay": copay_urgent},
                            "out_of_network": {"covered_as_in_network": True}
                        },
                        "emergency_room": {
                            "care": {
                                "in_network": {"copay": er_copay},
                                "out_of_network": {"covered_as_in_network": True}
                            },
                            "transportation": {
                                "in_network": {"copay": ambulance_copay},
                                "out_of_network": {"covered_as_in_network": True}
                            }
                        },
                        "diagnostic_test": {
                            "lab": {
                                "in_network": {"copay": lab_copay},
                                "out_of_network": {"coverage": "Not covered"}
                            }
                        },
                        "prescription_drugs": {
                            "tier_1": {
                                "in_network": {
                                    "retail_copay": generic_copay,
                                    "home_delivery_copay": generic_copay * 2
                                }
                            },
                            "tier_4": {
                                "in_network": {
                                    "retail_coinsurance": specialty_coinsurance,
                                    "retail_max": specialty_max
                                }
                            }
                        },
                        "hospital_stay": {
                            "facility_fee": {
                                "in_network": {"coinsurance": hospital_coinsurance},
                                "out_of_network": {"coverage": "Not covered"}
                            }
                        }
                    }

                    new_plan = InsurancePlan(
                        name=name,
                        type=plan_type,
                        insurer=insurer,
                        premium=premium,
                        deductibles={
                            "overall": deductible,
                            "family": family_deductible
                        },
                        out_of_pocket_limit={
                            "individual": out_of_pocket_max,
                            "family": family_out_of_pocket
                        },
                        referral_needed=referral_needed,
                        cost_sharing=cost_sharing,
                        services_covered_before_deductible=services_covered
                    )
                    st.session_state.plans[name] = new_plan
                    st.success(f"Added plan: {name}")
        
        # Display existing plans
        st.subheader("Existing Plans")
        
        with st.container(border=True):
            # Group plans by insurer
            insurers = {}
            for plan_name, plan in st.session_state.plans.items():
                if plan.insurer not in insurers:
                    insurers[plan.insurer] = []
                insurers[plan.insurer].append(plan)
            
            # Create list of plans to delete
            plans_to_delete = []
            
            # Display plans grouped by insurer
            for insurer in sorted(insurers.keys()):
                st.write(f"#### {insurer}")
                
                # Sort plans by premium
                insurers[insurer].sort(key=lambda x: x.premium)
                
                # Get list of plan names for this insurer (now in premium order)
                insurer_plans = [plan.name for plan in insurers[insurer]]
                
                # Create multi-select for deletion
                selected = st.multiselect(
                    "Select plans to delete",
                    options=insurer_plans,
                    key=f"delete_{insurer}"  # Unique key for each insurer's multiselect
                )
                
                # Add selected plans to deletion list
                plans_to_delete.extend(selected)
                
                # Display plan details in a clean format (already sorted by premium)
                for plan in insurers[insurer]:
                    st.write(f"- {plan.name} ({plan.type}): ${plan.premium:.2f}/month")
            
        # Add delete button if plans are selected
        if plans_to_delete:
            if st.button(f"Delete {len(plans_to_delete)} selected plan(s)", type="primary"):
                for plan_name in plans_to_delete:
                    del st.session_state.plans[plan_name]
                st.rerun()

if __name__ == "__main__":
    main() 