!> base of the models (not wrapped): inherited constant
module model_base_mod
  implicit none
  private
  public :: ModelBase_type

  type, abstract :: ModelBase_type
    integer :: stateSize = 2   !< size of the state
  end type ModelBase_type
end module model_base_mod
