! ifdef_mod: declarations at first use next to preprocessor regions
module ifdef_mod
  implicit none
contains
#ifdef OLD_INTERFACE
  !> old variant
  subroutine old_sum(x, s)
    real, intent(in) :: x(:)
    real, intent(out) :: s
    s = sum(x)
  end subroutine old_sum
#endif
  !> sums of blocks; the #endif before this procedure does not keep its declarations at the top
  subroutine block_sums(x, nb, s)
    integer, intent(in) :: nb          !< number of blocks
    real, intent(in) :: x(:)           !< values (nb blocks)
    real, intent(out) :: s             !< result
    integer :: i, j, rb, nr, nd
    real :: t
    s = 0.0
    nr = size(x) / nb
    do i = 1, nb
      rb = (i - 1) * nr
      do j = 1, nr
        t = x(rb + j)
        s = s + t
      end do
    end do
    nd = nb
#ifdef DEBUG
    print *, 'blocks', nd
#endif
  end subroutine block_sums
end module ifdef_mod
